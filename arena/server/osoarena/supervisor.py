"""Supervisor (privileged tier): `python -m osoarena.supervisor <worker name>`. Claims one queued job at a time, writes its inputs,
spawns one sandboxed child per job, waits (wall-clock timeout), validates the child's small JSON result and applies it to the DB.
Never imports or runs user data itself."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from osoarena import config, db, ladder, replays, tournament
from osoarena.config import LIMITS

RESULT_MAX = 8 << 20
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
             "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}

_stop = False


def claim(con, worker):
    with db.tx(con):
        return con.execute("UPDATE jobs SET status='running', worker=?, started_at=? WHERE id=(SELECT id FROM jobs WHERE "
                           "status='queued' ORDER BY priority, id LIMIT 1) RETURNING *", (worker, db.now())).fetchone()


def job_input(con, job):
    cfg = config.get()
    if job["kind"] == "check":
        m = con.execute("SELECT * FROM models WHERE id=?", (job["ref_id"],)).fetchone()
        return m and {"kind": "check", "path": str(cfg.models_dir / f"{m['sha256']}.safetensors")}
    if job["kind"] == "match":
        mt = con.execute("SELECT * FROM matches WHERE id=?", (job["ref_id"],)).fetchone()
        if not mt or mt["status"] not in ("queued", "running"):
            return None
        side = {}
        for k in ("a", "b"):
            m = con.execute("SELECT * FROM models WHERE id=?", (mt[f"{k}_id"],)).fetchone()
            if m["house"] == "bot":
                side[k] = "bot"
            elif m["house"] == "base":
                side[k] = db.base_model_info()["path"]
            else:
                side[k] = str(cfg.models_dir / f"{m['sha256']}.safetensors")
        con.execute("UPDATE matches SET status='running', started_at=? WHERE id=?", (db.now(), mt["id"]))
        return {"kind": "match", "a": side["a"], "b": side["b"], "seed": mt["seed"]}
    if job["kind"] == "replay":
        return {"kind": "replay", "log": str(cfg.logs_dir / f"{job['ref_id']}.osml.gz")}
    if job["kind"] == "fidelity":
        m = con.execute("SELECT * FROM models WHERE id=?", (job["ref_id"],)).fetchone()
        if not m or m["status"] != "active" or not m["style_profile"]:
            return None
        n = cfg.twins_recheck_matches if m["fidelity_at"] else max(cfg.twins_recheck_matches, cfg.twins_min_matches)
        logs = [[str(cfg.logs_dir / f"{mid}.osml.gz"), side] for mid, side in ladder.fidelity_matches(con, m["id"], n)]
        return {"kind": "fidelity", "profile": m["style_profile"], "build": ladder.build_of(m), "logs": logs}
    return None


def run_child(job_id, payload):
    """-> (result dict | None, wall seconds). None = the child was killed, timed out or wrote no valid result."""
    cfg = config.get()
    jd = cfg.jobs_dir / str(job_id)
    if jd.exists():
        shutil.rmtree(jd)
    old_umask = os.umask(0o007)  # group + setgid come from jobs/ (2750); chmod with S_ISGID is EPERM under RestrictSUIDSGID
    try:
        jd.mkdir(mode=0o770)
    finally:
        os.umask(old_umask)  # group oso-arena-sandbox (inherited from jobs/, setgid): the child may write here and nowhere else
    (jd / "job.json").write_text(json.dumps(payload))
    os.chmod(jd / "job.json", 0o640)
    wall = LIMITS[payload["kind"]][1]
    t0 = time.monotonic()
    if cfg.sandbox == "systemd":
        unit = f"oso-arena-child@{job_id}.service"
        try:
            subprocess.run(["systemctl", "start", unit], timeout=wall, capture_output=True)
        except subprocess.TimeoutExpired:
            subprocess.run(["systemctl", "stop", unit], capture_output=True)
        subprocess.run(["systemctl", "reset-failed", unit], capture_output=True)
    else:  # development: same entry point, same rlimits (set by the child), no user/network isolation
        p = subprocess.Popen([cfg.python, "-I", "-m", "osoarena.child", str(jd)], env=CLEAN_ENV, cwd=str(jd),
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, err = p.communicate(timeout=wall)
            if p.returncode:
                print(f"child {job_id} exit {p.returncode}: {err.decode(errors='replace')[-500:]}", flush=True)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    wall_s = time.monotonic() - t0
    rp = jd / "result.json"
    try:
        if rp.is_file() and rp.stat().st_size <= RESULT_MAX:
            res = json.loads(rp.read_text())
            if isinstance(res, dict):
                return res, wall_s
    except (OSError, ValueError):
        pass
    return None, wall_s


def _copy_out(src, dst):
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o640)
    os.replace(tmp, dst)


def valid_match_result(r):
    try:
        ok = (r.get("ok") is True and isinstance(r["voided"], bool) and len(r["score"]) == 2 and isinstance(r["games"], list)
              and len(r["games"]) <= 3 and all(len(g) == 2 for g in r["games"]) and isinstance(r["duration_s"], (int, float))
              and isinstance(r["rallies"], list) and len(r["rallies"]) <= 200)
        if ok and not r["voided"]:
            ok = r["winner"] in (0, 1)
        for x in r["rallies"]:
            ok = ok and x["server"] in "ab" and x["winner"] in "ab" and len(x["server"]) == 1 and len(x["winner"]) == 1 \
                and isinstance(x["t_start"], (int, float)) and isinstance(x["t_end"], (int, float)) and 1 <= int(x["game"]) <= 3 \
                and x["reason"] in ("missed", "out", "net", "serve", "net_touch", "ceiling", "let") and len(x["score"]) == 2
        if ok:  # keep only the documented fields
            r["rallies"] = [dict(game=int(x["game"]), t_start=float(x["t_start"]), t_end=float(x["t_end"]), server=x["server"],
                                 winner=x["winner"], reason=x["reason"], shots=int(x["shots"]), score=[int(x["score"][0]), int(x["score"][1])])
                            for x in r["rallies"]]
        return ok
    except (KeyError, TypeError, ValueError):
        return False


def valid_fidelity_result(r):
    """Keep only FidelityReport's documented fields, with their types (the sandbox's output is not trusted)."""
    try:
        rep = r["report"]
        num = lambda v: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e6)  # noqa: E731
        ok = (r.get("ok") is True and isinstance(rep["build_ok"], bool) and isinstance(rep["tempo_ok"], bool)
              and isinstance(rep["fidelity_ok"], bool) and num(rep["tempo_ratio"]) and num(rep["model_median_gap_s"])
              and num(rep["profile_median_gap_s"]) and isinstance(rep["gaps"], int) and num(rep.get("mix_js"))
              and isinstance(rep["reasons"], list) and len(rep["reasons"]) <= 10)
        if ok:
            r["report"] = {k: rep.get(k) for k in ("build_ok", "tempo_ratio", "tempo_ok", "model_median_gap_s", "profile_median_gap_s",
                                                   "gaps", "mix_js", "fidelity_ok")}
            r["report"]["reasons"] = [str(x)[:200] for x in rep["reasons"]]
        return ok
    except (KeyError, TypeError, ValueError):
        return False


def process(con, job):
    cfg = config.get()
    payload = job_input(con, job)
    if payload is None:
        with db.tx(con):
            con.execute("UPDATE jobs SET status='done', error='nothing to do', finished_at=? WHERE id=?", (db.now(), job["id"]))
        return
    res, wall_s = run_child(job["id"], payload)
    jd = cfg.jobs_dir / str(job["id"])
    err = None
    with db.tx(con):
        if job["kind"] == "check":
            ladder.apply_check(con, job["ref_id"], res)
        elif job["kind"] == "match":
            mt = con.execute("SELECT * FROM matches WHERE id=?", (job["ref_id"],)).fetchone()
            if res is not None and valid_match_result(res) and (jd / "match.osml.gz").is_file():
                _copy_out(jd / "match.osml.gz", cfg.logs_dir / f"{mt['id']}.osml.gz")
                ladder.apply_match_result(con, mt, res)
            else:
                err = (res or {}).get("error", "killed or timed out")
                ladder.note_match_error(con, mt)
            if mt["kind"] == "tournament":
                tournament.advance(con)
        elif job["kind"] == "fidelity":
            if res is not None and valid_fidelity_result(res):
                ladder.apply_fidelity(con, job["ref_id"], res["report"])
            else:
                err = (res or {}).get("error", "killed or timed out")  # not checked this time; the next pass retries
        elif job["kind"] == "replay":
            mid = job["ref_id"]
            if res is not None and res.get("ok") is True and (jd / "replay.bin.gz").is_file():
                cfg.replays_dir.mkdir(exist_ok=True)
                _copy_out(jd / "replay.bin.gz", cfg.replays_dir / f"{mid}.bin.gz")
                con.execute("UPDATE replays SET status='ready', size=?, raw_size=?, detail=NULL, last_access=? WHERE match_id=?",
                            ((cfg.replays_dir / f"{mid}.bin.gz").stat().st_size, int(res.get("raw_size") or 0), db.now(), mid))
            else:
                err = (res or {}).get("error", "killed or timed out")
                r = con.execute("SELECT attempts FROM replays WHERE match_id=?", (mid,)).fetchone()
                attempts = (r["attempts"] if r else 0) + 1
                if attempts <= 2:
                    con.execute("UPDATE replays SET attempts=?, status='building' WHERE match_id=?", (attempts, mid))
                    db.enqueue(con, "replay", mid)
                else:
                    con.execute("UPDATE replays SET attempts=?, status='failed', detail=? WHERE match_id=?",
                                (attempts, "The replay could not be built.", mid))
        cpu = (res or {}).get("cpu_s")
        con.execute("UPDATE jobs SET status=?, error=?, finished_at=?, wall_s=?, cpu_s=? WHERE id=?",
                    ("failed" if err else "done", str(err)[:300] if err else None, db.now(), round(wall_s, 2),
                     cpu if isinstance(cpu, (int, float)) else None, job["id"]))
    shutil.rmtree(jd, ignore_errors=True)
    if job["kind"] == "match" and not err:
        # Build the replay now (lowest queue priority, after checks and matches) so the first viewer doesn't wait for it.
        replays.request(con, job["ref_id"])
    print(f"job {job['id']} {job['kind']} {job['ref_id']}: {'error ' + str(err)[:120] if err else 'ok'} "
          f"wall {wall_s:.1f}s cpu {(res or {}).get('cpu_s')}s", flush=True)


def recover(con, worker):
    """Jobs this worker held when it died go back to the queue (their match/model rows too)."""
    with db.tx(con):
        for j in con.execute("SELECT * FROM jobs WHERE status='running' AND worker=?", (worker,)).fetchall():
            con.execute("UPDATE jobs SET status='queued', worker=NULL, started_at=NULL WHERE id=?", (j["id"],))
            if j["kind"] == "match":
                con.execute("UPDATE matches SET status='queued' WHERE id=? AND status='running'", (j["ref_id"],))


def main(argv):
    worker = argv[1] if len(argv) > 1 else "1"
    con = db.connect()
    db.init(con)  # idempotent; the supervisor may start before web has created the schema

    def stop(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    recover(con, worker)
    print(f"supervisor {worker} up (sandbox={config.get().sandbox})", flush=True)
    beat = 0.0
    while not _stop:
        if time.monotonic() - beat > 10:
            with db.tx(con):
                db.set_meta(con, f"worker:{worker}", db.now())
            beat = time.monotonic()
        job = claim(con, worker)
        if not job:
            time.sleep(0.5)
            continue
        try:
            process(con, job)
        except Exception as e:  # noqa: BLE001 -- never die on one bad job
            print(f"job {job['id']} crashed the supervisor step: {type(e).__name__}: {e}", flush=True)
            with db.tx(con):
                con.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                            (f"{type(e).__name__}"[:100], db.now(), job["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

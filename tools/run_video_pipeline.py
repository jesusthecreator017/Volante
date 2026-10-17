"""Two camera videos -> replay.bin, end to end (stages run as the scripts they are, so each can be rerun alone):
  1. pose2d_video.py   RTMPose + person detector per camera (slots assigned, other courts rejected)  -> pose2d_<cam>.npz
  2. shuttle2d_video.py  classical shuttle detector per camera                                      -> shuttle2d_<cam>.npz
  3. camsync.py        camera B's offset and rate against camera A from the shuttle and wrists    -> camsync.json
  4. pose3d.py         triangulation on camera A's clock                                         -> replay.bin (joints3d)
  5. shuttle3d.py      physics flights and contacts                                              -> + shuttle, events tracks
  6. oso-imu           racquet IMU fusion, swings and strokes (with --imu and --sync), else `--no-imu`:
                       one contact-only stroke row per shuttle contact                            -> + racquet (IMU only), swings tracks
  7. classify_strokes.py (only with --stroke-model) trained stroke labels replace the rule labels   -> swings.stroke_type
Run (repo root): tools/.venv/bin/python tools/run_video_pipeline.py --video-a A.mp4 --video-b B.mp4 --calib assets/calib/calib.json
                 [--imu imu.bin --sync sync.json] -o assets/replay/replay.bin [--work out/video]
Camera ids come from the calibration (cameras[0] is A, cameras[1] is B). Intermediate files stay in --work."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
PY = sys.executable


def run(cmd, cwd=ROOT):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"failed: {' '.join(str(c) for c in cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def stage(name, fn, summary):
    t = time.time()
    out = fn()
    secs = time.time() - t
    summary.append((name, secs))
    print(f"[{name}] {secs:.1f} s")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-a", type=Path, required=True)
    ap.add_argument("--video-b", type=Path, required=True)
    ap.add_argument("--calib", type=Path, required=True)
    ap.add_argument("--imu", type=Path)
    ap.add_argument("--sync", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=ROOT / "out/video")
    ap.add_argument("--detect-every", type=int, help="pose stage: run the person detector every N frames (default: pose2d_video.py's)")
    ap.add_argument("--profile", action="store_true", help="pose stage: print the per-stage time breakdown of each camera")
    ap.add_argument("--pose-min-score", type=float, help="pose3d --min-score (default: pose3d's 0.3)")
    ap.add_argument("--pose-max-gap", type=int, help="pose3d --max-gap (default: pose3d's 12)")
    ap.add_argument("--stroke-model", type=Path, help="BFMD-trained stroke classifier (Phase 10); runs after oso-imu (with or without --imu), absent = rule labels stay")
    ap.add_argument("--reuse-pose2d", action="store_true", help="skip the (slow) pose stage when pose2d_<cam>.npz already exist in --work")
    args = ap.parse_args()
    calib = json.loads(args.calib.read_text())
    a, b = (c["id"] for c in calib["cameras"][:2])
    vids = {a: args.video_a, b: args.video_b}
    w = args.work
    w.mkdir(parents=True, exist_ok=True)
    summary = []
    t_total = time.time()

    def pose2d():
        # The two cameras are independent: run them side by side, each with half of the cores so they do not fight
        threads = max(1, (os.cpu_count() or 2) // 2)
        extra = (["--detect-every", str(args.detect_every)] if args.detect_every else []) + (["--profile"] if args.profile else [])
        procs = [subprocess.Popen([PY, "-u", str(TOOLS / "pose2d_video.py"), "--video", str(vids[c]), "--calib", str(args.calib),
                                   "--camera-id", c, "-o", str(w / f"pose2d_{c}.npz"), "--threads", str(threads), *extra], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for c in (a, b)]
        for p in procs:
            out = p.communicate()[0]
            if p.returncode:
                raise SystemExit(out)
            keep = ("camera", "  ") if args.profile else ("camera",)
            print("".join(line + "\n" for line in out.splitlines()
                          if line.startswith(keep) and ("frames/s" in line or "ms/frame" in line or "profile" in line)))

    if args.reuse_pose2d and all((w / f"pose2d_{c}.npz").exists() for c in (a, b)):
        import numpy as np

        # The time it took when it ran (the two cameras side by side), so the summary and total stay comparable
        secs = max(float(np.load(w / f"pose2d_{c}.npz")["seconds"]) for c in (a, b))
        summary.append(("pose2d (cached)", secs))
        print(f"[pose2d] reusing pose2d_*.npz from {secs:.0f} s of inference")
    else:
        stage("pose2d", pose2d, summary)

    def shuttle2d():
        for c in (a, b):
            print(run([PY, TOOLS / "shuttle2d_video.py", "--video", vids[c], "--pose2d", w / f"pose2d_{c}.npz",
                       "-o", w / f"shuttle2d_{c}.npz"]).strip())

    stage("shuttle2d", shuttle2d, summary)

    def camsync():
        print(run([PY, TOOLS / "camsync.py", "--calib", args.calib, "--shuttle2d", w / f"shuttle2d_{a}.npz",
                   w / f"shuttle2d_{b}.npz", "--pose2d", w / f"pose2d_{a}.npz", w / f"pose2d_{b}.npz",
                   "-o", w / "camsync.json"]).strip())

    stage("camsync", camsync, summary)

    def pose3d():
        print(run([PY, TOOLS / "pose3d.py", "--calib", args.calib, "--pose2d", w / f"pose2d_{a}.npz", w / f"pose2d_{b}.npz",
                   "--camsync", w / "camsync.json", "-o", args.out,
                   *(["--min-score", args.pose_min_score] if args.pose_min_score is not None else []),
                   *(["--max-gap", args.pose_max_gap] if args.pose_max_gap is not None else [])]).strip())

    stage("pose3d", pose3d, summary)

    def shuttle3d():
        print(run([PY, TOOLS / "shuttle3d.py", "--calib", args.calib, "--shuttle2d", w / f"shuttle2d_{a}.npz",
                   w / f"shuttle2d_{b}.npz", "--replay", args.out, "--camsync", w / "camsync.json", "-o", args.out]).strip())

    stage("shuttle3d", shuttle3d, summary)

    def imu():
        imu_args = ["--imu", args.imu, "--sync", args.sync, "--player", "0"] if args.imu and args.sync else ["--no-imu"]
        print(run(["cargo", "run", "--release", "-q", "--bin", "oso-imu", "--", *imu_args, "--replay", args.out,
                   "-o", args.out]).strip())

    stage("oso-imu", imu, summary)

    if args.stroke_model:
        stage("strokes", lambda: print(run([PY, TOOLS / "classify_strokes.py", "--replay", args.out, "--model", args.stroke_model]).strip()),
              summary)

    print("stage summary:")
    for name, secs in summary:
        print(f"  {name:10s} {secs:8.1f} s")
    total = sum(secs for _, secs in summary)
    print(f"  {'total':10s} {total:8.1f} s -> {args.out}")
    (w / "run.json").write_text(json.dumps({"stages": dict(summary), "total_s": total}))


if __name__ == "__main__":
    main()

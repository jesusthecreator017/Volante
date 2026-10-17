"""Phase 9 end to end on rendered synthetic video (plain asserts, prints the numbers):
render_videos.py -> run_video_pipeline.py (out/video/replay.bin) -> checks against the ground truth in out/synth.
Run: tools/.venv/bin/python tools/test_video.py [--rerun | --rerun-pose]   (--rerun redoes the render and every stage, --rerun-pose
keeps the rendered videos and redoes the pipeline from the pose stage on (about 3 minutes with detect-every tracking); otherwise
existing outputs in out/video are reused). Phase 9.5 checks compare against out/video/baseline_phase9 (the Phase 9 outputs,
0.8 frames/s per camera)."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import pose2d_video as pv
import replay
from render_videos import OFFSET_B, RATE_B

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out/video"
BASE = OUT / "baseline_phase9"
CALIB = ROOT / "assets/calib/calib.json"
SYNTH = ROOT / "out/synth"
FPS = 60.0
N_FRAMES = 600
POSE3D = ["--pose-min-score", 0.15, "--pose-max-gap", 30]  # blocky renders score RTMPose 0.1-0.4 (real people 0.6-0.9)
DISTRACTOR_SHIFT, DISTRACTOR_Z = 3.7, 8.1  # mirror of src/state.rs


def sh(cmd):
    t = time.time()
    r = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"{cmd}\n{r.stdout}\n{r.stderr}"
    return r.stdout, time.time() - t


def ensure_outputs(rerun, rerun_pose=False):
    if rerun_pose and not rerun:
        out, secs = sh([sys.executable, ROOT / "tools/run_video_pipeline.py", "--video-a", OUT / "cam_a.mp4", "--video-b",
                        OUT / "cam_b.mp4", "--calib", CALIB, "-o", OUT / "replay.bin", "--work", OUT, "--profile", *POSE3D])
        print(out.strip(), f"\npipeline: {secs:.0f} s")
    if rerun or not (OUT / "cam_a.mp4").exists() or not (OUT / "truth.json").exists():
        out, secs = sh([sys.executable, ROOT / "tools/render_videos.py"])
        print(out.strip(), f"\nrender_videos: {secs:.0f} s")
    if rerun or not (OUT / "replay.bin").exists():
        out, secs = sh([sys.executable, ROOT / "tools/run_video_pipeline.py", "--video-a", OUT / "cam_a.mp4", "--video-b",
                        OUT / "cam_b.mp4", "--calib", CALIB, "-o", OUT / "replay.bin", "--work", OUT, *POSE3D])
        print(out.strip(), f"\npipeline: {secs:.0f} s")


def test_resample_and_estimator_on_ground_truth(calib):
    """No video: camsync's math alone. Linear tracks resample exactly; the GT shuttle seen by two cameras on skewed clocks gives
    back the offset and rate (noise-free detections, so the limit is the estimator, not the detector)."""
    import camsync
    t_b = np.arange(10) / FPS
    uv = np.stack([t_b * 100, t_b * -50], -1)
    out, sc = camsync.resample(t_b + 0.004, uv, np.ones(10), np.array([0.01, 0.1, 0.2]))
    assert np.allclose(out[:2], np.stack([(np.array([0.01, 0.1]) - 0.004) * 100, (np.array([0.01, 0.1]) - 0.004) * -50], -1))
    assert sc[0] > 0 and sc[1] > 0 and sc[2] == 0, "no extrapolation past the last frame"
    gap = np.ones(10)
    gap[4] = 0
    _, sc = camsync.resample(t_b, uv, gap, np.array([3.5 / FPS, 4.5 / FPS, 5.5 / FPS]))
    assert sc.tolist()[0] == 0 and sc.tolist()[1] == 0 and sc.tolist()[2] > 0, "no bridging over a missed frame"

    truth = json.loads((OUT / "truth.json").read_text())
    shuttle = replay.read(ROOT / "assets/replay/replay.bin")[1]["shuttle"]
    dets = []
    for cid in "ab":
        cam = pv.camera_by_id(calib, cid)
        t = np.array(truth["cameras"][cid]["t_render"])
        f = t * FPS
        i0 = np.minimum(np.floor(f).astype(int), N_FRAMES - 1)
        i1 = np.minimum(i0 + 1, N_FRAMES - 1)
        w = (f - np.floor(f))[:, None]
        pos = (1 - w) * shuttle[i0, :3] + w * shuttle[i1, :3]
        ok = (shuttle[i0, 6] > 0) & (shuttle[i1, 6] > 0) & (shuttle[i0, 7] == shuttle[i1, 7])
        uv = pv.project(cam, pos)
        ok &= (uv[:, 0] > 0) & (uv[:, 0] < 1920) & (uv[:, 1] > 0) & (uv[:, 1] < 1080)
        dets.append({"uv": uv.astype(np.float32), "score": ok.astype(np.float32), "t": np.arange(N_FRAMES) / FPS})
    est = camsync.estimate(calib, *dets)
    d_off, d_rate = (est["offset_b"] - OFFSET_B) * 1e3, (est["rate_b"] - 1 / RATE_B) * 1e6
    print(f"camsync on noise-free GT shuttle tracks: offset error {d_off:+.2f} ms, rate error {d_rate:+.1f} ppm, "
          f"{est['n_points']} points, ambiguous {est['ambiguous']}")
    assert abs(d_off) < 0.5 and abs(d_rate) < 50 and not est["ambiguous"]


def test_videos(calib):
    for c in "ab":
        cap = cv2.VideoCapture(str(OUT / f"cam_{c}.mp4"))
        assert cap.isOpened()
        assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == N_FRAMES, cap.get(cv2.CAP_PROP_FRAME_COUNT)
        assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (1920, 1080)
    # Distortion applied: the baseline projected through the lens bows away from its pinhole chord, and the white line is
    # in the video at the bowed position, not on the chord
    cam = pv.camera_by_id(calib, "a")
    cap = cv2.VideoCapture(str(OUT / "cam_a.mp4"))
    cap.read()
    ok, frame = cap.read()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(float)
    best = (0.0, None)
    for line in range(6):
        # court lines of constant z (running along x) and constant x
        zs = [-3.03, -2.57, 0.0, 2.57, 3.03]
        pts = np.array([[x, 0.0, zs[line % 5]] for x in np.linspace(-6.68, 6.68, 200)]) if line < 5 else \
            np.array([[-6.68, 0.0, z] for z in np.linspace(-3.03, 3.03, 100)])
        bowed = pv.project(cam, pts)
        flat = pv.project({**cam, "dm": np.zeros(5)}, pts)
        inside = np.ones(len(pts), bool)
        for q in (bowed, flat):
            inside &= (q[:, 0] > 5) & (q[:, 0] < 1915) & (q[:, 1] > 5) & (q[:, 1] < 1075)
        if inside.sum() < 20:
            continue
        b, f = bowed[inside], flat[inside]
        chord = b[-1] - b[0]
        n = np.array([-chord[1], chord[0]]) / np.linalg.norm(chord)
        sag = np.abs((b - b[0]) @ n).max()
        if sag > best[0]:
            best = (sag, (b, f))
    sag, (b, f) = best
    assert sag > 3.0, f"line sagitta {sag:.1f} px"

    def line_brightness(p):
        return np.mean([gray[int(round(y)) - 1:int(round(y)) + 2, int(round(x)) - 1:int(round(x)) + 2].max() for x, y in p])

    on, off = line_brightness(b), line_brightness(f)
    print(f"videos: 2 x {N_FRAMES} frames 1920x1080; line sagitta {sag:.1f} px, brightness on the distorted line {on:.0f} "
          f"vs on the pinhole position {off:.0f}")
    assert on > off + 30


def gt_people(truth, cam_id):
    """GT joint pixels of the 2 players and the 2 distractors at each frame's render time: [F, 4, 17, 2], and the camera dict."""
    calib = json.loads(CALIB.read_text())
    cam = pv.camera_by_id(calib, cam_id)
    g = np.load(SYNTH / "pose_gt.npz")["joints"]  # [F, 2, 17, 3]
    t = np.array(truth["cameras"][cam_id]["t_render"])
    fi = np.clip(np.round(t * FPS).astype(int), 0, N_FRAMES - 1)
    fd = np.round(((t + DISTRACTOR_SHIFT) % (N_FRAMES / FPS)) * FPS).astype(int) % N_FRAMES
    pts = np.concatenate([g[fi], g[fd] * [-1, 1, -1] + [0, 0, DISTRACTOR_Z]], 1)  # [F, 4, 17, 3]
    return pv.project(cam, pts.reshape(-1, 3)).reshape(len(t), 4, 17, 2), cam


def test_slots(truth, pdir=None, label=""):
    """Identity of the person each slot holds: every tracked keypoint votes for the owner of the nearest GT joint (of the 2 players
    and 2 distractors) within 30 px; the majority is the identity (RTMPose joints on these renders are noisy and a box can cover
    two overlapping people, so a distance to the whole skeleton is not a safe test)."""
    res = {}
    pdir = pdir or OUT
    for c in "ab":
        z = np.load(pdir / f"pose2d_{c}.npz")
        kp, foot = z["kp"], z["foot_xz"]
        gt, cam = gt_people(truth, c)
        has = kp[..., 2].max(-1) > 0  # [F, 2]
        right = swapped = leaked = lost = outside = 0
        for s in range(2):
            for f in np.flatnonzero(has[:, s]):
                ok = kp[f, s, :, 2] > 0.1
                d = np.linalg.norm(gt[f][:, :, None] - kp[f, s, ok, :2][None, None], axis=-1)  # [4, 17, K]
                nearest = d.min(1)  # [4, K]: per keypoint, distance to the closest joint of each person
                owner = nearest.argmin(0)[nearest.min(0) < 30.0]
                if len(owner) == 0:
                    lost += 1
                    continue
                k = int(np.bincount(owner, minlength=4).argmax())
                if k == s:
                    right += 1
                elif k < 2:
                    swapped += 1
                else:
                    leaked += 1
                if not np.isfinite(foot[f, s]).all() or not pv.in_court(foot[f, s]):
                    outside += 1
        n = N_FRAMES
        det = [int(has[:, s].sum()) for s in range(2)]
        print(f"slots{label} cam {c}: person assigned in {det[0]}/{n} (P0) and {det[1]}/{n} (P1) frames "
              f"(detected {z['detected'].tolist()}, coasted {z['coasted'].tolist()}); against GT: right {right}, swapped {swapped}, "
              f"distractor leaked {leaked}, no keypoint within 30 px of a GT joint {lost}; foot outside court +-1 m: {outside}; "
              f"{int(z['people'])} person boxes, {int(z['rejected'])} off-court rejections")
        # Spec: no slot holds a person whose feet are off our court, and P0/P1 never swap. The GT identity check is stricter: a
        # detector box that merges a player with an overlapping distractor can leak a frame or two (reported, bounded at 1 %)
        assert swapped == 0 and outside == 0
        assert leaked <= 0.01 * (right + swapped + leaked), f"{leaked} distractor frames"
        if leaked:
            print(f"  FINDING: {leaked} frames hold a distractor (merged person boxes), see out/video/samples/slot_a236.png")
        res[c] = dict(det=det, right=right, lost=lost, swapped=swapped, leaked=leaked)
    return res


def test_parity(slots, truth):
    """Phase 9.5: speed up without losing quality, against the Phase 9 outputs in out/video/baseline_phase9."""
    base = test_slots(truth, BASE, " (Phase 9 baseline)")
    for c in "ab":
        for p in range(2):
            now, was = slots[c]["det"][p] / N_FRAMES * 100, base[c]["det"][p] / N_FRAMES * 100
            print(f"parity cam {c} P{p}: detection rate {now:.1f} % vs baseline {was:.1f} % ({now - was:+.1f} points)")
            assert now >= was - 3.0, f"cam {c} P{p}: detection rate {now:.1f} % vs {was:.1f} %"
        assert slots[c]["swapped"] == 0
    errs = {}
    for name, path in (("now", OUT / "replay.bin"), ("baseline", BASE / "replay.bin")):
        j = replay.read(path)[1]["joints3d"]
        gt = np.load(SYNTH / "pose_gt.npz")["joints"]
        e = np.linalg.norm(j[..., :3] - gt, axis=-1)
        errs[name] = e[j[..., 3] > 0].mean()
    print(f"parity MPJPE {errs['now'] * 100:.1f} cm vs baseline {errs['baseline'] * 100:.1f} cm")
    assert errs["now"] <= errs["baseline"] + 0.03, errs  # Phase 9.6: lower thresholds trade a little accuracy for coverage
    c = replay.read(OUT / "replay.bin")[1]["joints3d"][..., 3] > 0
    hips = c[:, :, 11:13].all(-1).mean(0)
    print(f"coverage: joints {c.mean() * 100:.1f} % (per player {np.round(c.mean((0, 2)) * 100, 1).tolist()}), "
          f"both hips per player {np.round(hips * 100, 1).tolist()} %")
    assert (hips >= 0.35).all(), hips


def test_speed():
    fps = [N_FRAMES / float(np.load(OUT / f"pose2d_{c}.npz")["seconds"]) for c in "ab"]
    print(f"speed: pose {fps[0]:.2f} / {fps[1]:.2f} frames/s per camera (both running)")
    assert min(fps) >= 10.0, f"{fps} frames/s, spec 10"


def test_coreml_vs_cpu(n=20):
    """The CoreML sessions against the CPU ones on n frames of camera a: RTMPose keypoints (on the Phase 9 person boxes) within 2 px
    and scores within 0.05; YOLOX boxes (on a 640x640 tile of the court) within 2 px, the same count."""
    import onnxruntime as ort
    if "CoreMLExecutionProvider" not in ort.get_available_providers():
        print("coreml check: CoreML EP not available, CPU used")
        return
    cpu, ml = pv.make_body(4, coreml=False), pv.make_body(4, coreml=True)
    boxes = np.load(BASE / "pose2d_a.npz")["boxes_all"]
    cap = cv2.VideoCapture(str(OUT / "cam_a.mp4"))
    dk = ds = dd = 0.0
    for f in range(0, N_FRAMES, N_FRAMES // n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        b = boxes[f][np.isfinite(boxes[f, :, 0])]
        if not ok:
            continue
        tile = img[300:940, 500:1140]
        d0, d1 = cpu.det_model(tile), ml.det_model(tile)
        assert len(d0) == len(d1), f"frame {f}: {len(d0)} CPU boxes, {len(d1)} CoreML boxes"
        dd = max(dd, float(np.abs(np.array(d0) - np.array(d1)).max()) if len(d0) else 0.0)
        if not len(b):
            continue
        (k0, s0), (k1, s1) = cpu.pose_model(img, bboxes=b), ml.pose_model(img, bboxes=b)
        dk, ds = max(dk, float(np.abs(k0 - k1).max())), max(ds, float(np.abs(s0 - s1).max()))
    print(f"coreml vs cpu pose on {n} frames: max keypoint diff {dk:.3f} px, max score diff {ds:.4f}; "
          f"detector max box diff {dd:.3f} px")
    assert dk < 2.0 and ds < 0.05 and dd < 2.0, f"CoreML differs: {dk} px, {ds}, {dd} px"


def test_camsync():
    cs = json.loads((OUT / "camsync.json").read_text())
    truth = json.loads((OUT / "truth.json").read_text())["estimator"]
    d_off = (cs["offset_b"] - truth["offset_b"]) * 1e3
    d_rate = (cs["rate_b"] - truth["rate_b"]) * 1e6
    print(f"camsync: offset_b {cs['offset_b'] * 1e3:.2f} ms (truth {truth['offset_b'] * 1e3:.2f}, error {d_off:+.2f} ms), "
          f"rate_b {cs['rate_b']:.7f} (truth {truth['rate_b']:.7f}, error {d_rate:+.1f} ppm), capped rms {cs['cost']:.2f} px over "
          f"{cs['n_points']} points, ambiguous {cs['ambiguous']}")
    assert not cs["ambiguous"]
    assert abs(d_off) < 1.0, f"offset error {d_off:.2f} ms"
    # Rev: rate is reported, not asserted against truth (40 ppm over a 10 s clip is below detection noise); fixed to 1 here
    assert cs["rate_b"] == 1.0 and cs["rate_fixed"], cs


def test_pose(slots):
    header, tracks = replay.read(OUT / "replay.bin")
    j = tracks["joints3d"]
    gt = np.load(SYNTH / "pose_gt.npz")["joints"]
    conf = j[..., 3]
    err = np.linalg.norm(j[..., :3] - gt, axis=-1)
    rates = {c: [s / N_FRAMES for s in slots[c]["det"]] for c in "ab"}
    for p in range(2):
        e, cf = err[:, p], conf[:, p]
        used = cf > 0
        print(f"pose P{p}: detection rate cam a {rates['a'][p] * 100:.0f} %, cam b {rates['b'][p] * 100:.0f} %; 3D joints tracked "
              f"{used.mean() * 100:.1f} %, MPJPE {e[used].mean() * 100:.1f} cm (median {np.median(e[used]) * 100:.1f} cm)"
              if used.any() else f"pose P{p}: no 3D joints")
    low = min(min(v) for v in rates.values())
    mp = err[conf > 0].mean() if (conf > 0).any() else np.inf
    if low >= 0.70:
        assert mp < 0.10, f"MPJPE {mp * 100:.1f} cm"
    else:
        print(f"FINDING: pose detection rate {low * 100:.0f} % < 70 % on the blocky renders (RTMPose is trained on real people); "
              f"numbers above, sample frames in out/video/samples; not a FAIL per the spec")
    return header, tracks


def test_shuttle(truth, tracks):
    gt = np.load(SYNTH / "shuttle_gt.npz")
    calib = json.loads(CALIB.read_text())
    sh_replay = replay.read(ROOT / "assets/replay/replay.bin")[1]["shuttle"]  # what the video was rendered from
    for c in "ab":
        z = np.load(OUT / f"shuttle2d_{c}.npz")
        cam = pv.camera_by_id(calib, c)
        t = np.array(truth["cameras"][c]["t_render"])
        pos = np.full((N_FRAMES, 3), np.nan)
        for i, ti in enumerate(t):
            f = ti * FPS
            i0 = min(int(np.floor(f)), N_FRAMES - 1)
            i1 = min(i0 + 1, N_FRAMES - 1)
            a, b = sh_replay[i0], sh_replay[i1]
            if a[6] > 0 and b[6] > 0 and a[7] == b[7]:
                pos[i] = (1 - (f - np.floor(f))) * a[:3] + (f - np.floor(f)) * b[:3]
        vis = np.isfinite(pos[:, 0])
        uv = np.full((N_FRAMES, 2), np.nan)
        uv[vis] = pv.project(cam, pos[vis])
        inframe = vis & (uv[:, 0] >= 0) & (uv[:, 0] < 1920) & (uv[:, 1] >= 0) & (uv[:, 1] < 1080)
        det = z["score"] > 0
        hit = inframe & det & (np.linalg.norm(z["uv"] - uv, axis=1) < 5.0)
        print(f"shuttle cam {c}: detected {hit.sum()}/{inframe.sum()} in-frame GT frames ({hit.sum() / inframe.sum() * 100:.0f} %), "
              f"{int((det & ~hit).sum())} other detections of {int(det.sum())}")
    ev = tracks["events"]
    contacts = ev[ev[:, 1] == 1][:, 0]
    err = np.array([np.abs(contacts - ct).min() * 1e3 if len(contacts) else np.inf for ct in gt["contacts"][:, 0]])
    ok = int((err < 20).sum())
    print(f"shuttle 3D: {len(contacts)} contacts found, {ok} of {len(err)} GT contacts matched within 20 ms "
          f"(errors ms: {np.round(err, 1).tolist()})")
    if ok < 5:  # Rev: 5/7 is a target, not a gate, with the classical shuttle detector
        print(f"FINDING: only {ok} of {len(err)} contacts within 20 ms (target 5)")


def test_replay_loads():
    out, _ = sh(["cargo", "run", "--release", "-q", "--", "--check-replay", OUT / "replay.bin"])
    print(out.strip())
    assert ": ok," in out


STROKE_MODEL = Path.home() / "Datasets/BFMD-work/stroke_hgb.joblib"


def test_no_imu_strokes():
    """Phase 11: a video-only replay gets one contact-only swings row per events contact; the trained model then labels them."""
    tmp = OUT / "replay_strokes.bin"
    out, _ = sh(["cargo", "run", "--release", "-q", "--bin", "oso-imu", "--", "--replay", OUT / "replay.bin", "--no-imu", "-o", tmp])
    print(out.strip().splitlines()[-1])
    tr = replay.read(tmp)[1]
    contacts = int((tr["events"][:, 1] == 1).sum())
    sw = tr["swings"]
    assert len(sw) == contacts > 0 and not np.isnan(sw[:, 3]).any(), f"{len(sw)} swings rows for {contacts} contacts"
    assert "racquet" not in tr
    if not STROKE_MODEL.exists():
        print(f"  skipped the model check: {STROKE_MODEL} not found")
        return
    out, _ = sh([sys.executable, ROOT / "tools/classify_strokes.py", "--replay", tmp, "--model", STROKE_MODEL])
    print(out.strip().splitlines()[-1])
    assert len(replay.read(tmp)[1]["swings"]) == contacts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--rerun-pose", action="store_true")
    args = ap.parse_args()
    ensure_outputs(args.rerun, args.rerun_pose)
    calib = json.loads(CALIB.read_text())
    truth = json.loads((OUT / "truth.json").read_text())
    failures = []

    def check(name, fn, *a):
        # Every check runs and prints its numbers; the spec thresholds that are missed are listed at the end (exit code 1)
        try:
            return fn(*a)
        except AssertionError as e:
            failures.append(f"{name}: {e}")
            print(f"  FAIL {name}: {e}")

    check("estimator on GT tracks", test_resample_and_estimator_on_ground_truth, calib)
    check("videos", test_videos, calib)
    slots = check("slots", test_slots, truth)
    check("camsync", test_camsync)
    tracks = check("pose", test_pose, slots)
    check("shuttle", test_shuttle, truth, tracks[1] if tracks else replay.read(OUT / "replay.bin")[1])
    check("replay loads", test_replay_loads)
    check("no-imu strokes", test_no_imu_strokes)
    if BASE.exists() and slots:
        check("parity", test_parity, slots, truth)
        check("speed", test_speed)
        check("coreml vs cpu", test_coreml_vs_cpu)
    run = json.loads((OUT / "run.json").read_text()) if (OUT / "run.json").exists() else {}
    pose_fps = [N_FRAMES / float(np.load(OUT / f"pose2d_{c}.npz")["seconds"]) for c in "ab"]
    print(f"timings: render {truth.get('render_fps')} frames/s (2496x1404 incl. PNG encode); pose {pose_fps[0]:.2f} / {pose_fps[1]:.2f} "
          f"frames/s per camera (two run side by side); pipeline total {run.get('total_s', float('nan')):.0f} s {run.get('stages')}")
    assert (OFFSET_B, RATE_B) == (0.0073, 1.00004)
    if failures:
        print("test_video: FAILED " + "; ".join(failures))
        sys.exit(1)
    print("test_video: ok")


if __name__ == "__main__":
    main()

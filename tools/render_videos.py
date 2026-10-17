"""Synthetic camera videos: the viewer renders the match from each calibrated camera (pinhole, wider than the sensor), then
the lens distortion of calib.json is applied with cv2.remap and the frames are written as mp4 (mp4v; no ffmpeg needed).
Camera b gets a deliberate clock offset and rate mismatch, and distractor players stand on the neighbouring court.
Run (repo root): tools/.venv/bin/python tools/render_videos.py [--out out/video] [--t1 10]
Writes cam_a.mp4, cam_b.mp4 and truth.json (`estimator`: the camsync.json convention t_common = rate_b * t_b + offset_b)."""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OFFSET_B, RATE_B = 0.0073, 1.00004
MARGIN = 1.3  # render this much wider than the sensor so the barrel-distorted corners are filled


def distortion_maps(cam, render_size):
    """cv2.remap maps: for each video pixel (distorted) the pixel of the pinhole render that shows the same ray."""
    W, H = cam["image_size"]
    K, dist = np.array(cam["K"]), np.array(cam["dist"])
    u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    n = cv2.undistortPoints(np.stack([u, v], -1).reshape(-1, 1, 2), K, dist).reshape(H, W, 2)
    rw, rh = render_size
    # The render's optical axis is its geometric center, (rw - 1) / 2 in OpenCV pixel coordinates
    mx = n[..., 0] * K[0, 0] + (rw - 1) / 2
    my = n[..., 1] * K[1, 1] + (rh - 1) / 2
    return mx.astype(np.float32), my.astype(np.float32)


def render(camera, out_dir, size, t1, offset, rate, fps):
    cmd = ["cargo", "run", "--release", "-q", "--", "--render-frames", str(out_dir), "--camera", camera,
           "--t0", "0", "--t1", str(t1), "--fps", str(fps), "--rate", str(rate), "--offset", str(offset),
           "--size", f"{size[0]}x{size[1]}", "--distractors"]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    t = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip(), time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=Path, default=ROOT / "assets/calib/calib.json")
    ap.add_argument("--out", type=Path, default=ROOT / "out/video")
    ap.add_argument("--t1", type=float, default=10.0)
    ap.add_argument("--keep-frames", action="store_true")
    args = ap.parse_args()
    calib = json.loads(args.calib.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    truth = {"fps": calib["cameras"][0]["fps"], "cameras": {}, "render_fps": {}}
    fps = truth["fps"]
    for cam, offset, rate in zip(calib["cameras"][:2], (0.0, OFFSET_B), (1.0, RATE_B)):
        W, H = cam["image_size"]
        size = (int(np.ceil(W * MARGIN / 2) * 2), int(np.ceil(H * MARGIN / 2) * 2))
        frames = args.out / f"frames_{cam['id']}"
        msg, secs = render(cam["id"], frames, size, args.t1, offset, rate, fps)
        print(f"camera {cam['id']}: {msg} (render {size[0]}x{size[1]}, process {secs:.1f} s)")
        truth["render_fps"][cam["id"]] = float(msg.rsplit("=", 1)[1].split()[0])  # "... = 31.2 frames/s"
        mx, my = distortion_maps(cam, size)
        path = args.out / f"cam_{cam['id']}.mp4"
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        files = sorted(frames.glob("*.png"))
        t = time.time()
        for f in files:
            img = cv2.imread(str(f))
            vw.write(cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
        vw.release()
        print(f"  {len(files)} frames distorted + encoded to {path} in {time.time() - t:.1f} s")
        n = len(files)
        # Frame i is rendered at offset + i / (fps * rate); the camera's own timestamp is i / fps
        truth["cameras"][cam["id"]] = {"offset": offset, "rate": rate, "n_frames": n,
                                       "t_render": (offset + np.arange(n) / (fps * rate)).tolist()}
        if not args.keep_frames:
            shutil.rmtree(frames)
    truth["estimator"] = {"offset_b": OFFSET_B, "rate_b": 1.0 / RATE_B,
                          "convention": "t_common = rate_b * t_b + offset_b, t_b = frame / fps"}
    (args.out / "truth.json").write_text(json.dumps(truth))


if __name__ == "__main__":
    main()

"""Classical shuttle detector baseline -> shuttle2d_<cam>.npz (format: docs/data-format.md). The swap-in point for TrackNetV3 or a
similar network: anything that writes the same npz (uv, score, t, camera_id, model) replaces this file, nothing downstream changes.
Run: python shuttle2d_video.py --video cam_a.mp4 --pose2d ../out/video/pose2d_a.npz -o ../out/video/shuttle2d_a.npz
Per frame: grayscale, min(|f_t - f_t-1|, |f_t+1 - f_t|) (the AND of the two frame differences: only what is at its place in
frame t moves), threshold, 2x2 opening, connected components of 2-150 px that contain a white pixel (all channels >= 130), drop blobs touching a person box (all people the pose
stage detected, including other courts), then the candidate nearest to a constant-velocity prediction from the previous
detections (gate 60 px; if none, a blob that differs from the static background, for the slow top of a flight) or, when the track is lost,
the strongest one. Score = blob contrast / 60 (capped at 1).
Known gaps: the shuttle near the top of a flight barely moves (the difference vanishes), near a player it is masked, and
clutter that moves (a light flicker, another court's shuttle) competes. TrackNet is the upgrade."""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np

DIFF_THR = 25
AREA = (2, 150)
GATE_PX = 60.0
LOST_AFTER = 4  # frames without a detection before the constant-velocity prediction is dropped
LEAD_PX = 1.5
WHITE_MIN = 130
CONFIRM = 3  # detections in a row before a new track is output
BOX_GROW = 20  # px added around each person box
CONTRAST_FULL = 60.0


def candidates(prev, cur, nxt, boxes, cur_bgr=None):
    """prev/cur/nxt: uint8 gray; cur_bgr the colour frame of `cur`. Returns [(u, v, contrast)] of the moving small white blobs
    in `cur` (a shuttle is white: some pixel of the blob has all three channels >= WHITE_MIN; players, racquets and shadows are not)."""
    d = np.minimum(cv2.absdiff(cur, prev), cv2.absdiff(nxt, cur))
    mask = (d > DIFF_THR).astype(np.uint8)
    for b in boxes:
        x0, y0, x1, y1 = (np.round(b) + [-BOX_GROW, -BOX_GROW, BOX_GROW, BOX_GROW]).astype(int)
        mask[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        if not AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= AREA[1]:
            continue
        x, y, w, h = stats[i, :4]
        if cur_bgr is not None:
            patch = cur_bgr[max(y - 2, 0):y + h + 2, max(x - 2, 0):x + w + 2]
            if patch.min(axis=2).max() < WHITE_MIN:
                continue
        sel = labels[y:y + h, x:x + w] == i
        wgt = d[y:y + h, x:x + w][sel].astype(np.float64)
        yy, xx = np.nonzero(sel)
        out.append(((xx * wgt).sum() / wgt.sum() + x, (yy * wgt).sum() / wgt.sum() + y, wgt.mean()))
    return out


def background(cap, step=10):
    """Per-pixel median of every `step`-th gray frame: the static scene (players and the shuttle move too much to stay)."""
    frames, i = [], 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if i % step == 0:
            frames.append(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        i += 1
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def refine(cand, cur, bg, vel=None, half=10):
    """The frame difference smears the shuttle along its motion; its place in this frame alone is the blob of |frame - background|
    around the candidate (intensity-weighted centroid), which has no smear. Falls back to the candidate."""
    u, v, c = cand
    x0, y0 = max(int(round(u)) - half, 0), max(int(round(v)) - half, 0)
    d = cv2.absdiff(cur[y0:int(round(v)) + half + 1, x0:int(round(u)) + half + 1],
                    bg[y0:int(round(v)) + half + 1, x0:int(round(u)) + half + 1]).astype(np.float64)
    d[d < DIFF_THR] = 0
    if d.sum() <= 0:
        return cand
    # only the blob nearest the candidate: connected component of the nonzero mask containing/nearest it
    n, labels = cv2.connectedComponents((d > 0).astype(np.uint8), connectivity=8)
    ys, xs = np.nonzero(labels)
    if n < 2:
        return cand
    k = labels[ys[np.argmin((xs + x0 - u) ** 2 + (ys + y0 - v) ** 2)], xs[np.argmin((xs + x0 - u) ** 2 + (ys + y0 - v) ** 2)]]
    m = labels == k
    w = d[m]
    yy, xx = np.nonzero(m)
    if vel is not None and np.hypot(*vel) > 1.0:
        # The silhouette's centroid sits mid-skirt and moves with the viewing angle; the leading end (the cork) is the same
        # 3D point in both cameras: centroid of the pixels within LEAD_PX of the front along the motion
        ahead = (xx + x0) * vel[0] / np.hypot(*vel) + (yy + y0) * vel[1] / np.hypot(*vel)
        lead = ahead >= ahead.max() - LEAD_PX
        if w[lead].sum() > 0:
            return ((xx[lead] * w[lead]).sum() / w[lead].sum() + x0, (yy[lead] * w[lead]).sum() / w[lead].sum() + y0, c)
    return ((xx * w).sum() / w.sum() + x0, (yy * w).sum() / w.sum() + y0, c)


def pick(cands, history):
    """Candidate closest to the constant-velocity prediction (history: [(frame, u, v)], newest last) within GATE_PX; with no
    usable history the strongest candidate."""
    if not cands:
        return None
    if history:
        f, u, v = history[-1]
        pred = np.array([u, v])
        if len(history) >= 2:
            f0, u0, v0 = history[-2]
            pred = pred + (np.array([u, v]) - [u0, v0]) / max(f - f0, 1)
        dist = [np.hypot(c[0] - pred[0], c[1] - pred[1]) for c in cands]
        k = int(np.argmin(dist))
        return cands[k] if dist[k] <= GATE_PX else None
    return max(cands, key=lambda c: c[2])


def run(video, pose2d, out, camera_id=None, max_frames=None, log=print):
    cap = cv2.VideoCapture(str(video))
    z = np.load(pose2d)
    camera_id = camera_id or str(z["camera_id"])
    boxes_all = z["boxes_all"]
    ms, uv, score, history, window, pending = [], [], [], [], [], []  # window: the last three gray frames
    colour = []  # the last three colour frames
    t0 = time.time()
    bg = background(cap)
    while max_frames is None or len(ms) < max_frames:
        ok, img = cap.read()
        if not ok:
            break
        ms.append(cap.get(cv2.CAP_PROP_POS_MSEC))
        window = (window + [cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)])[-3:]
        colour = (colour + [img])[-3:]
        uv.append((0.0, 0.0))
        score.append(0.0)
        f = len(ms) - 2  # the middle frame of the window
        if len(window) < 3:
            continue
        boxes = boxes_all[f][np.isfinite(boxes_all[f, :, 0])] if f < len(boxes_all) else []
        cands = candidates(*window, boxes, colour[1])
        # What differs from the static background finds the shuttle where it barely moves (the top of a flight); it is only
        # trusted to continue a track (too much static clutter to start one from)
        still = candidates(bg, window[1], bg, boxes, colour[1])
        if history and f - history[-1][0] > LOST_AFTER:
            history, pending = [], []
        best = pick(cands, history)
        if best is None and history:
            best = pick(still, history)
        if best is not None:
            vel = None
            if len(history) >= 2:
                (f0, u0, v0), (f1, u1, v1) = history[-2:]
                vel = ((u1 - u0) / max(f1 - f0, 1), (v1 - v0) / max(f1 - f0, 1))
            best = refine(best, window[1], bg, vel)
            pending.append((f, best))
            history = (history + [(f, best[0], best[1])])[-2:]
            # A track is believed once CONFIRM consecutive detections agree with the motion model (the first one is a guess)
            if len(pending) >= CONFIRM:
                for pf, pb in pending[-CONFIRM:] if len(pending) == CONFIRM else pending[-1:]:
                    uv[pf], score[pf] = pb[:2], min(pb[2] / CONTRAST_FULL, 1.0)
    n = len(ms)
    uv, score = np.array(uv, np.float32), np.array(score, np.float32)
    secs = time.time() - t0
    t = np.asarray(ms, np.float64) / 1000.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    if n < 2 or t[-1] <= 0:
        t = np.arange(n) / fps
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, uv=uv, score=score, t=t, camera_id=camera_id, model="classical-framediff")
    log(f"camera {camera_id}: {n} frames in {secs:.1f} s = {n / secs:.1f} frames/s, shuttle in {int((score > 0).sum())} frames")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--pose2d", type=Path, required=True, help="pose2d_video.py output (person boxes to mask)")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    run(args.video, args.pose2d, args.out)


if __name__ == "__main__":
    main()

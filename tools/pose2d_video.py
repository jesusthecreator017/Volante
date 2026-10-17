"""2D pose from a camera video with rtmlib (YOLOX person detector + RTMPose-m, ONNX Runtime, CoreML EP with CPU fallback) -> pose2d_<cam>.npz
(format: docs/data-format.md), keeping only the two players of OUR court (people on other courts are rejected).
Run: python pose2d_video.py --video cam_a.mp4 --calib ../assets/calib/calib.json --camera-id a -o ../out/video/pose2d_a.npz
Models (YOLOX-m humanart 90 MB, RTMPose-m body7 48 MB) download on first use into ~/.cache/rtmlib/hub/checkpoints/.

Detection: the YOLOX ONNX has a fixed 640x640 input, so a whole 1080p frame shrinks a player to ~30 px and is mostly missed.
The detector therefore runs on native-resolution 640x640 tiles over the image region where the calibration says people can
stand (our court plus the neighbouring one), merged with NMS. A slot whose person was not detected is tracked from its previous
pose box for up to MAX_COAST frames (the pose network runs on the expanded previous box).
Slots: the foot point (mean of the ankles, else the hips dropped to the floor, else the bottom of the person box) is back-projected to the floor y = 0 with the
calibration (undistort + ray-plane intersection); people landing outside our court +- COURT_MARGIN are rejected (the box bottom must land inside too); P0 is the one
with x < 0, P1 with x > 0 (singles players do not cross the net); two claimants of a slot: the nearer to its previous position.

Speed (Phase 9.5): every frame the pose network runs on the expanded box of each slot's previous keypoints (tracking). The detector
runs every DETECT_EVERY frames, and (at most every RETRY_EVERY frames) when a slot's tracked pose fails. A detection frame tiles
only around the slots that need it (the scheduled ones: both), a full people-region sweep every FULL_SWEEP_EVERY frames or when a
slot has been lost longer than it can coast. A tracked pose counts as a coast in the output counters. --profile prints a per-stage
breakdown. Providers: both networks use the ONNX Runtime CoreML EP (MLProgram) when available, --provider cpu to disable;
outputs match the CPU ones (test_video.py checks)"""
import argparse
import json
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np

from court import HALF_LENGTH, HALF_WIDTH

COURT_MARGIN = 1.0
MIN_SCORE = 0.3
HIP_HEIGHT = 0.9  # m, where a hip ray meets the world when the ankles are not tracked
MAX_COAST = 8
TRACK_MIN_SCORE = 0.15  # keypoints that make the tracking box (spec 0.3; the blocky renders score 0.1-0.4, so 0.3 leaves < 4)
COAST_MIN_MEAN_SCORE = 0.15  # RTMPose scores on these blocky renders are 0.1-0.4 (real people 0.6-0.9)
TILE, TILE_OVERLAP = 640, 160
DETECT_EVERY = 6
FULL_SWEEP_EVERY = 30
TRACK_MARGIN = 1.5  # m around a tracked player's feet that a detection frame still tiles
MAX_STEP = 0.4  # a tracked box may move by this fraction of its size between two frames (feet jitter on the floor plane; the box does not)
RETRY_EVERY = 1  # while a slot is lost, the detector is retried at most this often (the pose network keeps tracking in between)
LOST_SWEEP_EVERY = 6  # a slot lost for more than MAX_COAST frames: full people-region sweep this often (else tile around where it was)
REACH, REACH_PER_FRAME = 1.5, 0.15  # m: how far from its last position a lost slot may pick up a person, and the growth per lost frame
MAX_FOOT_STEP = 1.25  # m a tracked foot may move between two frames (ankle jitter on the floor plane is below this, a hop to the other player is not)
MIN_DET_OVERLAP = 0.4  # IoU a detection needs with the tracked box of its slot to replace the tracked pose
OTHERS_KEEP = 12  # frames an untracked person's box from a detection frame stays in boxes_all
MAX_OVERLAP = 0.5  # IoU of two tracked boxes above which both slots are on the same person
MAX_BOX_CHANGE = 1.4  # a tracked box whose size changes by more than this factor (linear) in one frame means the track is lost (spec 2x; 1.4 keeps a player from hopping to a distractor behind)
NOMINAL = np.array([[-HALF_LENGTH / 2, 0.0], [HALF_LENGTH / 2, 0.0]])  # where a slot's player starts (court x, z)


def camera_by_id(calib, cam_id):
    cam = next(c for c in calib["cameras"] if c["id"] == cam_id)
    return {**cam, "Rm": np.array(cam["R"], float), "tv": np.array(cam["t"], float), "Km": np.array(cam["K"], float),
            "dm": np.array(cam["dist"], float)}


def to_plane(cam, uv, height=0.0):
    """Pixels (distorted) -> court (x, z) where the viewing ray crosses the plane y = height. NaN when it does not."""
    uv = np.asarray(uv, np.float64).reshape(-1, 1, 2)
    n = cv2.undistortPoints(uv, cam["Km"], cam["dm"]).reshape(-1, 2)
    d = np.hstack([n, np.ones((len(n), 1))]) @ cam["Rm"]  # R^T applied to row vectors: ray direction in court coords
    c = -cam["Rm"].T @ cam["tv"]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (height - c[1]) / d[:, 1]
    s = np.where(s > 0, s, np.nan)
    p = c + s[:, None] * d
    return p[:, [0, 2]]


def project(cam, xyz):
    rvec, _ = cv2.Rodrigues(cam["Rm"])
    uv, _ = cv2.projectPoints(np.asarray(xyz, np.float64).reshape(-1, 1, 3), rvec, cam["tv"], cam["Km"], cam["dm"])
    return uv.reshape(-1, 2)


def people_region(cam, width, height, z_span=(-5.0, 12.0), x_half=HALF_LENGTH + 1.5, top=2.4):
    """Image rectangle (x0, y0, x1, y1) covering where people can stand, from the calibration: floor and head-height
    corners of the court strip z_span (our court and the neighbouring one), the ones in front of the camera."""
    pts = np.array([[x, y, z] for x in (-x_half, x_half) for y in (0.0, top) for z in z_span])
    depth = (pts @ cam["Rm"].T + cam["tv"])[:, 2]
    # Corners behind the camera cannot be projected and are skipped
    ok = depth > 0.3
    uv = project(cam, pts[ok])
    pad = 60
    x0, y0 = np.floor(uv.min(0)).astype(int) - pad
    x1, y1 = np.ceil(uv.max(0)).astype(int) + pad
    return int(max(x0, 0)), int(max(y0, 0)), int(min(x1, width)), int(min(y1, height))


def _starts(n, size, overlap):
    if n <= size:
        return [0]
    k = int(np.ceil((n - overlap) / (size - overlap)))
    return [int(v) for v in np.linspace(0, n - size, k).round()]


def merge_regions(regions):
    """Union rectangles that overlap, so no pixel is tiled twice."""
    regions = [list(r) for r in regions]
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    regions[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del regions[j]
                    merged = True
                    break
            if merged:
                break
    return regions


def detect_people(det, img, regions):
    """YOLOX over native-resolution tiles of the rectangles `regions` (x0, y0, x1, y1); returns boxes [N, 4] x0 y0 x1 y1 in image
    pixels (NMS merged)."""
    boxes = []
    for x0, y0, x1, y1 in merge_regions(regions):
        sub = img[y0:y1, x0:x1]
        for ty in _starts(sub.shape[0], TILE, TILE_OVERLAP):
            for tx in _starts(sub.shape[1], TILE, TILE_OVERLAP):
                for b in det(sub[ty:ty + TILE, tx:tx + TILE]):
                    boxes.append([b[0] + tx + x0, b[1] + ty + y0, b[2] + tx + x0, b[3] + ty + y0])
    boxes = np.array(boxes, np.float32).reshape(-1, 4)
    if len(boxes) > 1:
        keep = cv2.dnn.NMSBoxes([[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes], [1.0] * len(boxes), 0.5, 0.4)
        boxes = boxes[np.array(keep, int).reshape(-1)]
    return boxes


def track_region(cam, full, foot, box, margin=TRACK_MARGIN, top=2.4, pad=60):
    """Image rectangle around a tracked player: the floor and head-height corners margin metres around the feet (court x, z),
    united with the player's last box, clipped to `full` (the people region)."""
    pts = np.array([[foot[0] + dx, y, foot[1] + dz] for dx in (-margin, margin) for y in (0.0, top) for dz in (-margin, margin)])
    ok = (pts @ cam["Rm"].T + cam["tv"])[:, 2] > 0.3
    uv = project(cam, pts[ok]) if ok.any() else np.zeros((0, 2))
    if box is not None:
        uv = np.vstack([uv, box.reshape(2, 2)])
    if not len(uv):
        return full
    lo, hi = np.floor(uv.min(0)).astype(int) - pad, np.ceil(uv.max(0)).astype(int) + pad
    # ponytail: at most one detector tile per player (the 640 px around the middle); the 1.5 m margin is narrower on a close player
    mid = (lo + hi) // 2
    lo, hi = np.where(hi - lo > TILE, mid - TILE // 2, lo), np.where(hi - lo > TILE, mid + TILE // 2, hi)
    r = (max(lo[0], full[0]), max(lo[1], full[1]), min(hi[0], full[2]), min(hi[1], full[3]))
    return r if r[2] > r[0] and r[3] > r[1] else full


def make_body(threads, coreml=True):
    """rtmlib Body (YOLOX-m + RTMPose-m) whose ONNX Runtime sessions use `threads` intra-op threads (CPU fallback ops) and run on the
    CoreML EP (MLProgram, CPU fallback for unsupported nodes) when it is available and `coreml` is set. rtmlib builds its own
    CPU sessions first; they are replaced here (no fork)."""
    import onnxruntime as ort
    from rtmlib import Body

    body = Body(mode="balanced", backend="onnxruntime", device="cpu")
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.log_severity_level = 3
    # YOLOX-m needs RequireStaticInputShapes (else the CoreML EP fails on its NMS outputs); RTMPose-m with it falls back to the CPU
    for model, static in ((body.det_model, True), (body.pose_model, False)):
        prov = ["CPUExecutionProvider"]
        if coreml and "CoreMLExecutionProvider" in ort.get_available_providers():
            opts = {"ModelFormat": "MLProgram", **({"RequireStaticInputShapes": "1"} if static else {})}
            prov = [("CoreMLExecutionProvider", opts), "CPUExecutionProvider"]
        model.session = ort.InferenceSession(model.onnx_model, so, providers=prov)
    return body


class Profile:
    """Wall time per stage (seconds) and call count."""

    def __init__(self):
        self.secs, self.calls = defaultdict(float), defaultdict(int)

    @contextmanager
    def __call__(self, name):
        t = time.perf_counter()
        yield
        self.secs[name] += time.perf_counter() - t
        self.calls[name] += 1

    def report(self, frames, log):
        total = sum(self.secs.values()) or 1.0
        for k, v in sorted(self.secs.items(), key=lambda kv: -kv[1]):
            log(f"  {k:10s} {v:8.2f} s {v / total * 100:5.1f} %  {v / frames * 1e3:7.1f} ms/frame  {self.calls[k]:5d} calls")


def iou(a, b):
    w, h = np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])
    inter = max(w, 0) * max(h, 0)
    return inter / (np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - inter)


def foot_point(cam, kp, box=None):
    """kp [17, 3] -> court (x, z) of the person's feet: the ankles, else the hips at HIP_HEIGHT, else (a detector box was
    given) the bottom middle of the box; None when nothing is usable."""
    ank = kp[[15, 16]]
    ank = ank[ank[:, 2] >= MIN_SCORE]
    if len(ank):
        return to_plane(cam, ank[:, :2].mean(0))[0]
    hip = kp[[11, 12]]
    hip = hip[hip[:, 2] >= MIN_SCORE]
    if len(hip):
        return to_plane(cam, hip[:, :2].mean(0), HIP_HEIGHT)[0]
    if box is not None:
        return to_plane(cam, [(box[0] + box[2]) / 2, box[3]])[0]
    return None


def box_foot(cam, box):
    """Court (x, z) under the bottom middle of a person box (None without a box)."""
    return None if box is None else to_plane(cam, [(box[0] + box[2]) / 2, box[3]])[0]


def in_court(xz):
    return abs(xz[0]) <= HALF_LENGTH + COURT_MARGIN and abs(xz[1]) <= HALF_WIDTH + COURT_MARGIN


def box_inside(cam, box):
    """Second opinion on the floor position: the box bottom must land on our court too. Ankles of a distant person on the
    neighbouring court are often placed a little low, and on a grazing ray that moves the floor point by metres (into our court);
    the bottom of its detector box does not."""
    xz = box_foot(cam, box)
    return xz is None or (np.isfinite(xz).all() and in_court(xz))


def assign_slots(cam, people, prev, slots=(0, 1), age=(99, 99)):
    """people: list of (kp [17, 3], tag). prev: last court (x, z) of each slot [2, 2] (NaN unknown).
    `slots`: the slots being searched (a detection around one lost slot must not hand its person to the other slot).
    `age`: frames since each slot last held a person; a claimant further from the slot's last position than REACH + REACH_PER_FRAME
    * age metres is someone else (an ankle on the wrong side of the net must not hand a player to the other slot).
    Returns (chosen index per slot or -1, foot points per person, number rejected as off-court)."""
    feet = [foot_point(cam, kp, box) for kp, _, box in people]
    claims = [[], []]
    rejected = 0
    for i, xz in enumerate(feet):
        if xz is None or not np.isfinite(xz).all():
            continue
        if not in_court(xz) or not box_inside(cam, people[i][2]):
            rejected += 1
            continue
        slot = 0 if xz[0] < 0 else 1
        if slot in slots and not (np.isfinite(prev[slot]).all() and np.linalg.norm(xz - prev[slot]) > REACH + REACH_PER_FRAME * age[slot]):
            claims[slot].append(i)
    chosen = [-1, -1]
    for s in (0, 1):
        if claims[s]:
            ref = prev[s] if np.isfinite(prev[s]).all() else NOMINAL[s]
            chosen[s] = min(claims[s], key=lambda i: np.linalg.norm(feet[i] - ref))
    return chosen, feet, rejected


def box_of(kp, grow=1.25, min_score=TRACK_MIN_SCORE):
    ok = kp[:, 2] >= min_score
    if ok.sum() < 4:
        return None
    lo, hi = kp[ok, :2].min(0), kp[ok, :2].max(0)
    c, h = (lo + hi) / 2, (hi - lo) * grow / 2 + 8
    return np.array([*(c - h), *(c + h)], np.float32)


def read_timestamps(cap_ms, n, fps):
    """Per-frame timestamps in seconds from the container (variable frame rate safe); frame / fps if they are all 0."""
    t = np.asarray(cap_ms, np.float64) / 1000.0
    return t if n > 1 and t[-1] > 0 else np.arange(n) / fps


def run(video, calib, cam_id, out, max_frames=None, log=print, detect_every=DETECT_EVERY, threads=None, coreml=True,
        profile=False):
    cam = camera_by_id(calib, cam_id)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or cam["fps"]
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    threads = threads or max(1, (os.cpu_count() or 2) // 2)  # two camera processes side by side
    body = make_body(threads, coreml)
    region = people_region(cam, width, height)
    log(f"camera {cam_id}: {video.name} {width}x{height} @ {fps:g} fps, people region {region}, detect every {detect_every}, "
        f"{threads} threads, providers {body.det_model.session.get_providers()[0][:6]}/{body.pose_model.session.get_providers()[0][:6]}")
    kps, ms, boxes_all, feet_out = [], [], [], []
    prev, age, last_kp = np.full((2, 2), np.nan), [99, 99], [None, None]
    stats = dict(frames=0, detected=np.zeros(2, int), coasted=np.zeros(2, int), rejected=0, people=0, det_frames=0, tiles=0)
    prof = Profile()
    since_det = since_full = 10 ** 9
    others = []
    t0 = time.time()

    def valid(slot, kp, box):
        """A pose on an expanded box is believed when it is solid, on our court on the right side of the net."""
        xz = foot_point(cam, kp, box)
        return (kp[:, 2].mean() >= COAST_MIN_MEAN_SCORE and xz is not None and np.isfinite(xz).all()
                and in_court(xz) and box_inside(cam, box) and (xz[0] < 0) == (slot == 0)), xz

    def to_people(k, s, tag, bxs):
        return [(np.concatenate([k[i], np.clip(s[i], 0, 1)[:, None]], 1), tag, bxs[i]) for i in range(len(bxs))]

    while max_frames is None or len(ms) < max_frames:
        with prof("decode"):
            ok, img = cap.read()
        if not ok:
            break
        ms.append(cap.get(cv2.CAP_PROP_POS_MSEC))
        since_full += 1
        people, feet, chosen, rej, boxes = [], [], [-1, -1], 0, np.zeros((0, 4), np.float32)
        # Tracking: the pose network on the expanded box of each slot's last pose (cheap); a slot that fails (weak pose, off court,
        # jumped, box grew or shrank more than 2x) is lost and the detector is due, at most every RETRY_EVERY frames
        track = [s for s in (0, 1) if last_kp[s] is not None and age[s] < MAX_COAST]
        tb = [box_of(last_kp[s]) for s in track]
        track = [s for s, b in zip(track, tb) if b is not None]
        tb = [b for b in tb if b is not None]
        got = {}
        if track:
            with prof("track"):
                k, sc = body.pose_model(img, bboxes=np.array(tb))
                for j, slot in enumerate(track):
                    kp = np.concatenate([k[j], np.clip(sc[j], 0, 1)[:, None]], 1)
                    g = box_of(kp)
                    xz = None if g is None else foot_point(cam, kp, g)
                    if g is not None and (xz is None or not np.isfinite(xz).all() or not in_court(xz)):
                        xz = box_foot(cam, g)  # ankles of a steadily tracked player can land far off: the box bottom is the second opinion
                    good = (g is not None and kp[:, 2].mean() >= COAST_MIN_MEAN_SCORE and np.isfinite(xz).all() and in_court(xz)
                            and (xz[0] < 0) == (slot == 0))
                    if good and g is not None and box_inside(cam, g):  # the new pose box must also stand on our court
                        ratio = np.sqrt(np.prod(g[2:] - g[:2]) / np.prod(tb[j][2:] - tb[j][:2]))
                        moved = np.linalg.norm((g[:2] + g[2:]) / 2 - (tb[j][:2] + tb[j][2:]) / 2) / (tb[j][2:] - tb[j][:2]).max()
                        held = age[slot] == 0  # only a pose held last frame has a position to be continuous with
                        if (1 / MAX_BOX_CHANGE <= ratio <= MAX_BOX_CHANGE
                                and not (held and (moved > MAX_STEP or np.linalg.norm(xz - prev[slot]) > MAX_FOOT_STEP))):
                            got[slot] = (kp, xz, tb[j])
                # Both slots on the same person (a player at the net): the one whose box moved more lost its player
                if len(got) == 2 and iou(box_of(got[0][0]), box_of(got[1][0])) > MAX_OVERLAP:
                    move = {s: np.linalg.norm(box_of(got[s][0])[:2] - got[s][2][:2]) for s in got}
                    del got[max(move, key=move.get)]
        scheduled = since_det + 1 >= detect_every
        lost = len(got) < 2
        detected = scheduled or (lost and since_det + 1 >= RETRY_EVERY)
        if not detected:
            for slot, (kp, xz, b) in got.items():
                people.append((kp, "coast", b))
                feet.append(xz)
                chosen[slot] = len(people) - 1
            boxes = np.array([g[2] for g in got.values()], np.float32).reshape(-1, 4)
            since_det += 1
        else:
            # Detection frame. Tile only around the slots' last positions, except for the periodic full sweep (and a more frequent
            # one while a slot has been lost for longer than it can coast)
            full = (since_full >= FULL_SWEEP_EVERY or not np.isfinite(prev).all()
                    or (max(age) > MAX_COAST and since_full >= LOST_SWEEP_EVERY))
            # A lost slot is searched where it was; the other one (tracked fine) only on the scheduled detections
            searched = [s for s in (0, 1) if full or scheduled or s not in got]
            with prof("tiling"):
                regions = [region] if full else [track_region(cam, region, prev[s], box_of(last_kp[s])) for s in searched]
            with prof("detector"):
                boxes = detect_people(body.det_model, img, regions)
            since_det, since_full = 0, 0 if full else since_full
            stats["det_frames"] += 1
            if len(boxes):
                with prof("pose"):
                    k, sc = body.pose_model(img, bboxes=boxes)
                people = to_people(k, sc, "det", boxes)
            with prof("assign"):
                chosen, feet, rej = assign_slots(cam, people, prev, searched, age)
            # Slots with no detection (or a detection nowhere near the tracked player: the ankles of a person at the net can land on
            # the wrong side of it): the tracked pose, when it was good
            for slot, (kp, xz, b) in got.items():
                if chosen[slot] >= 0 and iou(people[chosen[slot]][2], b) < MIN_DET_OVERLAP:
                    chosen[slot] = -1
                if chosen[slot] < 0:
                    people.append((kp, "coast", b))
                    feet.append(xz)
                    chosen[slot] = len(people) - 1
        with prof("write"):
            frame = np.zeros((2, 17, 3), np.float32)
            foot = np.full((2, 2), np.nan)
            for slot, i in enumerate(chosen):
                if i < 0:
                    age[slot] += 1
                    continue
                frame[slot] = people[i][0]
                foot[slot] = feet[i]
                prev[slot], last_kp[slot], age[slot] = feet[i], people[i][0], 0
                stats["detected" if people[i][1] == "det" else "coasted"][slot] += 1
            kps.append(frame)
            feet_out.append(foot)
            # Person boxes for the shuttle detector's mask: this frame's, plus the other people seen on a recent detection frame
            # (distractors are not tracked) that no current box overlaps
            others = [(b, a + 1) for b, a in others if a < OTHERS_KEEP and all(iou(b, c) < 0.3 for c in boxes)]
            shown = np.vstack([boxes, *[b[None] for b, _ in others]]) if others else boxes
            if detected:
                others += [(b, 0) for b in boxes]
            boxes_all.append(np.vstack([shown, np.full((8 - min(len(shown), 8), 4), np.nan)])[:8])
            stats["frames"] += 1
            stats["rejected"] += rej
            stats["people"] += len(boxes)
        if stats["frames"] % 100 == 0:
            log(f"  {stats['frames']} frames, {stats['frames'] / (time.time() - t0):.2f} frames/s")
    secs = time.time() - t0
    n = len(kps)
    t = read_timestamps(ms, n, fps)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, kp=np.stack(kps), t=t, frame=np.arange(n), camera_id=cam_id, model="rtmpose-m", keypoint_format="coco17",
             foot_xz=np.stack(feet_out), boxes_all=np.stack(boxes_all).astype(np.float32),
             detected=stats["detected"], coasted=stats["coasted"], rejected=stats["rejected"], people=stats["people"],
             seconds=secs)
    log(f"camera {cam_id}: {n} frames in {secs:.1f} s = {n / secs:.2f} frames/s; per slot detected {stats['detected']} "
        f"coasted/tracked {stats['coasted']} of {n}; detector ran on {stats['det_frames']} frames; {stats['people']} people boxes, "
        f"{stats['rejected']} off-court rejections")
    if profile:
        log(f"camera {cam_id} profile:")
        prof.report(max(n, 1), log)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--calib", type=Path, required=True)
    ap.add_argument("--camera-id", required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--detect-every", type=int, default=DETECT_EVERY, help="run the person detector every N frames, track in between (1 = every frame)")
    ap.add_argument("--threads", type=int, help="ONNX Runtime intra-op threads (default: cores // 2, for two cameras side by side)")
    ap.add_argument("--provider", choices=["auto", "cpu"], default="auto", help="auto = CoreML for the pose network when available")
    ap.add_argument("--profile", action="store_true", help="print the per-stage time breakdown")
    args = ap.parse_args()
    run(args.video, json.loads(args.calib.read_text()), args.camera_id, args.out, args.max_frames, detect_every=args.detect_every,
        threads=args.threads, coreml=args.provider == "auto", profile=args.profile)


if __name__ == "__main__":
    main()

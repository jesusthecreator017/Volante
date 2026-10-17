"""Draw a pose2d npz (boxes_all grey, slot keypoints P0 red / P1 blue) on one video frame: overlay_pose.py cam_a.mp4 pose2d_a.npz FRAME out.png"""
import sys

import cv2
import numpy as np

video, npz, f, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
z = np.load(npz)
cap = cv2.VideoCapture(video)
cap.set(cv2.CAP_PROP_POS_FRAMES, f)
ok, img = cap.read()
assert ok, f"cannot read frame {f}"
for b in z["boxes_all"][f]:
    if np.isfinite(b).all():
        cv2.rectangle(img, tuple(int(v) for v in b[:2]), tuple(int(v) for v in b[2:]), (160, 160, 160), 2)
for slot, col in enumerate([(0, 0, 255), (255, 0, 0)]):
    k = z["kp"][f, slot]
    for x, y, s in k:
        if np.isfinite(x) and s > 0.1:
            cv2.circle(img, (int(x), int(y)), 4, col, -1)
cv2.imwrite(out, img)

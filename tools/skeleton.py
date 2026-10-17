"""COCO-17 keypoint order and bones.

NOTE: BONES mirrors src/replay.rs; both files must change together.
"""
NAMES = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
         "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]
J = {n: i for i, n in enumerate(NAMES)}
BONES = [(0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
         (11, 13), (13, 15), (12, 14), (14, 16)]
# Limb chains an occlusion can hide (synth_motion.py)
CHAINS = {"l_arm": (5, 7, 9), "r_arm": (6, 8, 10), "l_leg": (11, 13, 15), "r_leg": (12, 14, 16)}
# Joints smoothed with the higher cutoff (pose3d.py): fast swings
FAST = (7, 8, 9, 10)

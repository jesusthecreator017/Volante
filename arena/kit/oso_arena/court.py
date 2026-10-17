"""Court landmarks in court coordinates (meters, +Y up, +X along length, +Z across width).

NOTE: a copy of tools/court.py (the kit ships alone); these constants mirror crates/oso-render/src/scene/court.rs.
test_badsim.py asserts the copy equals tools/court.py; all must change together.
"""
import numpy as np

HALF_LENGTH = 6.70
HALF_WIDTH = 3.05
SINGLES_HALF_WIDTH = 2.59
SHORT_SERVICE_X = 1.98
LONG_SERVICE_X = 5.94
LINE_WIDTH = 0.04
NET_TOP_POST = 1.55
NET_TOP_CENTER = 1.524

# Lines lie inside the measurement, so the painted centerline is half a width inside it.
_h = LINE_WIDTH / 2
X_LINES = {  # lines of constant x, as |x| of the centerline
    "baseline": HALF_LENGTH - _h,
    "long_service": LONG_SERVICE_X - _h,
    "short_service": SHORT_SERVICE_X - _h,
}
Z_LINES = {  # lines of constant z, as |z| of the centerline
    "doubles_side": HALF_WIDTH - _h,
    "singles_side": SINGLES_HALF_WIDTH - _h,
    "center": 0.0,
}
_SIGN = {-1: "n", 1: "p"}


def _build():
    lm = {}
    for xn, xv in X_LINES.items():
        for sx in (-1, 1):
            for zn, zv in Z_LINES.items():
                for sz in ((1,) if zn == "center" else (-1, 1)):
                    zs = "" if zn == "center" else "_" + _SIGN[sz]
                    lm[f"{xn}_{_SIGN[sx]}__{zn}{zs}"] = (sx * xv, 0.0, sz * zv)
    lm["post_top_near"] = (0.0, NET_TOP_POST, -HALF_WIDTH)
    lm["post_top_far"] = (0.0, NET_TOP_POST, HALF_WIDTH)
    return lm


LANDMARKS = _build()


def court_polylines():
    """3D segments (centerlines + net top) as a list of (p0, p1)."""
    segs = []
    zb = HALF_WIDTH - _h
    xb = HALF_LENGTH - _h
    for s in (-1, 1):
        for xv in X_LINES.values():
            segs.append(((s * xv, 0, -zb), (s * xv, 0, zb)))
        for zn in ("doubles_side", "singles_side"):
            z = s * Z_LINES[zn]
            segs.append(((-xb, 0, z), (xb, 0, z)))
        segs.append(((s * X_LINES["short_service"], 0, 0), (s * xb, 0, 0)))
    # net top: post to center to post
    segs.append(((0, NET_TOP_POST, -HALF_WIDTH), (0, NET_TOP_CENTER, 0)))
    segs.append(((0, NET_TOP_CENTER, 0), (0, NET_TOP_POST, HALF_WIDTH)))
    return [(np.array(a, float), np.array(b, float)) for a, b in segs]

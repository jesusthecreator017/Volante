"""Click court landmarks on a frame. python pick_points.py --image f.png | --video v.mp4 --frame N -o pts.json
left click = set, s = skip, u = undo, q = save & quit"""
import argparse
import json

import cv2

from court import LANDMARKS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--video")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    if args.video:
        cap = cv2.VideoCapture(args.video)
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.frame)
        ok, img = cap.read()
        if not ok:
            raise SystemExit("cannot read frame")
    else:
        img = cv2.imread(args.image)
    names, points, history = list(LANDMARKS), {}, []  # history: names in the order they were visited
    state = {"i": 0}
    win = "pick"

    def draw():
        view = img.copy()
        for n, (u, v) in points.items():
            cv2.circle(view, (int(u), int(v)), 4, (0, 255, 0), -1)
        cv2.imshow(win, view)
        i = state["i"]
        cv2.setWindowTitle(win, f"[{i}/{len(names)}] click: {names[i]}  (s skip, u undo, q save)" if i < len(names)
                           else "done - press q")

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and state["i"] < len(names):
            points[names[state["i"]]] = [float(x), float(y)]
            history.append(names[state["i"]])
            state["i"] += 1
            draw()

    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    draw()
    while True:
        k = cv2.waitKey(50) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s") and state["i"] < len(names):
            history.append(None)
            state["i"] += 1
        elif k == ord("u") and history:
            state["i"] -= 1
            points.pop(history.pop(), None)
        else:
            continue
        draw()
    with open(args.output, "w") as f:
        json.dump(points, f, indent=1)
    print(f"saved {len(points)} points")


if __name__ == "__main__":
    main()

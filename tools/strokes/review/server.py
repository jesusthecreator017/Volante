#!/usr/bin/env python3
"""Phase 16.4a: local review tool for the stroke dataset's review queue. Binds 127.0.0.1 only, stdlib only, nothing is uploaded.

    tools/.venv/bin/python tools/strokes/review/server.py --reviewer R1 [--root ~/Datasets/oso-strokes] [--version 1] [--port 8765]

Reads  ROOT/processed/swings_v<N>.npz        (the queue = swings with status `review`)
Writes ROOT/labels/<session>.csv             (source=review; a re-label replaces the swing's row). Then re-run build_dataset.py."""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import features as F  # noqa: E402
from build_dataset import DEFAULT_ROOT, DISCARD, LABEL_FIELDS, load  # noqa: E402

MAX_BODY = 4096
MAX_POINTS = 250
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
REVIEWER_RE = re.compile(r"^R\d{1,3}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/review.js": ("review.js", "text/javascript; charset=utf-8"),
          "/review.css": ("review.css", "text/css; charset=utf-8")}
HINT = "after reviewing, re-run: tools/.venv/bin/python tools/strokes/build_dataset.py --root {root} --version {version}"


def host_ok(value):
    """A Host (host[:port]) or Origin (scheme://host[:port]) header value that points at this machine (DNS-rebinding guard)."""
    if not value:
        return False
    h = value.split("://", 1)[-1].rstrip("/")
    h = h.rsplit(":", 1)[0] if re.search(r":\d+$", h) else h
    return h in LOCAL_HOSTS


def decimate(a, how="mean"):
    """[500, k] -> [250, k] by averaging (or max for flags) neighbouring samples."""
    a = np.asarray(a, float)
    a = a[: len(a) // 2 * 2].reshape(-1, 2, a.shape[1])
    return (a.max(axis=1) if how == "max" else a.mean(axis=1))


class Store:
    """The npz plus the label CSVs under root/labels (the only files this tool writes)."""

    def __init__(self, root, version, reviewer):
        self.root, self.reviewer, self.version = Path(root).expanduser().resolve(), reviewer, version
        self.d = load(self.root / "processed" / f"swings_v{version}.npz")
        self.index = {s: i for i, s in enumerate(self.d["swing_id"].tolist())}
        self.queue = [i for i, st in enumerate(self.d["status"].tolist()) if st == "review"]

    def side_of(self, i):
        k = int(self.d["side"][i])
        return F.SIDES[k] if k >= 0 else ""

    def csv_path(self, session):
        if not SESSION_RE.match(session):
            raise ValueError("bad session name")
        return self.root / "labels" / f"{session}.csv"

    def rows(self, session):
        p = self.csv_path(session)
        if not p.is_file():
            return {}
        with p.open(newline="", encoding="utf-8") as f:
            return {r["swing_id"]: r for r in csv.DictReader(f)}

    def overrides(self):
        out = {}
        for session in sorted({str(self.d["session"][i]) for i in self.queue}):
            out.update(self.rows(session))
        return out

    def queue_json(self):
        ov = self.overrides()
        items = []
        for i in self.queue:
            sid = str(self.d["swing_id"][i])
            r = ov.get(sid)
            items.append(dict(id=sid, session=str(self.d["session"][i]), block=int(self.d["block"][i]), label=str(self.d["label"][i]),
                              side=self.side_of(i), reasons=str(self.d["reasons"][i]), notes=str(self.d["notes"][i]) if "notes" in self.d else "",
                              override=dict(cls=r["class"], side=r["side"]) if r else None))
        return dict(items=items, classes=F.CLASSES, version_hint=HINT.format(root=self.root, version=self.version))

    def swing_json(self, sid, cls=None, side=None):
        i = self.index[sid]
        x = self.d["x"][i]
        n = x.shape[0]
        t_ms = (np.arange(0, n, 2) * F.DT - F.PRE_S) * 1000.0
        cmp_cls = cls if cls in F.CLASSES else str(self.d["label"][i])
        cmp_side = "" if cmp_cls in F.SIDELESS else (side if side in F.SIDES else self.side_of(i))
        sel = (self.d["status"] == "labelled") & (self.d["label"] == cmp_cls) & (self.d["side"] == (F.SIDES.index(cmp_side) if cmp_side else -1))
        mean = self.d["x"][sel][:, :, 6:8].mean(axis=0) if sel.any() else None
        r = lambda a: np.round(a, 2).T.tolist()  # noqa: E731
        return dict(id=sid, t_ms=np.round(t_ms, 1).tolist(), w=r(decimate(x[:, 6:7]))[0], a=r(decimate(x[:, 7:8]))[0],
                    axes=r(decimate(x[:, :6])), sat=(decimate(x[:, 8:10], "max") > 0).any(axis=1).astype(int).tolist(),
                    mean_w=r(decimate(mean[:, 0:1]))[0] if mean is not None else None,
                    mean_a=r(decimate(mean[:, 1:2]))[0] if mean is not None else None,
                    compare=dict(cls=cmp_cls, side=cmp_side, n=int(sel.sum())))

    def set_label(self, sid, cls=None, side="", clear=False):
        """Write (or, for `clear`, remove) the swing's row; -> the new override or None."""
        i = self.index[sid]
        session = str(self.d["session"][i])
        rows = self.rows(session)
        if clear:
            rows.pop(sid, None)
        else:
            rows[sid] = dict(swing_id=sid, t_peak_us=int(self.d["t_peak_us"][i]), **{"class": cls}, side=side, source="review",
                             reviewer=self.reviewer)
        p = self.csv_path(session)
        p.parent.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, LABEL_FIELDS, lineterminator="\n")
            w.writeheader()
            for k in sorted(rows):
                w.writerow({f: rows[k][f] for f in LABEL_FIELDS})
        os.replace(tmp, p)
        return None if clear else dict(cls=cls, side=side)


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "oso-review"

        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def guard(self):
            if not host_ok(self.headers.get("Host")) or (self.headers.get("Origin") and not host_ok(self.headers.get("Origin"))):
                self.send(403, {"error": "not a local request"})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            u = urlsplit(self.path)
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                return self.send(200, (HERE / name).read_bytes(), ctype)
            if u.path == "/api/queue":
                return self.send(200, store.queue_json())
            m = re.fullmatch(r"/api/swing/(.+)", u.path)
            if m:
                sid = unquote(m.group(1))
                if sid not in store.index:
                    return self.send(404, {"error": "unknown swing"})
                q = parse_qs(u.query)
                return self.send(200, store.swing_json(sid, (q.get("class") or [None])[0], (q.get("side") or [None])[0]))
            self.send(404, {"error": "not found"})

        def do_POST(self):
            if not self.guard():
                return
            if urlsplit(self.path).path != "/api/label":
                return self.send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.send(411, {"error": "Content-Length required"})
            if n < 0 or n > MAX_BODY:
                return self.send(413, {"error": f"body over {MAX_BODY} bytes"})
            try:
                b = json.loads(self.rfile.read(n))
                assert isinstance(b, dict)
            except (ValueError, AssertionError):
                return self.send(400, {"error": "body must be a JSON object"})
            sid, cls, side = b.get("swing_id"), b.get("class"), b.get("side") or ""
            if not isinstance(sid, str) or sid not in store.index:
                return self.send(404, {"error": "unknown swing"})
            if b.get("clear") is True:  # undo of a first label
                return self.send(200, {"override": store.set_label(sid, clear=True)})
            if cls != DISCARD and cls not in F.CLASSES:
                return self.send(400, {"error": "unknown class"})
            if cls in (DISCARD, *F.SIDELESS):
                if side:
                    return self.send(400, {"error": f"{cls} takes no side"})
            elif side not in F.SIDES:
                return self.send(400, {"error": "side must be FH or BH"})
            self.send(200, {"override": store.set_label(sid, cls, side)})

        def do_PUT(self):
            self.send(405, {"error": "method not allowed"})

        do_DELETE = do_PATCH = do_PUT

    return Handler


def serve(root, version, reviewer, port=8765, host="127.0.0.1"):
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("refusing to bind anywhere but 127.0.0.1")
    if not REVIEWER_RE.match(reviewer):
        raise SystemExit("--reviewer must be a pseudonymous ID like R1 (never a name)")
    return HTTPServer(("127.0.0.1", port), make_handler(Store(root, version, reviewer)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    s = serve(a.root, a.version, a.reviewer, a.port, a.host)
    print(f"review tool on http://127.0.0.1:{s.server_port}/  (Ctrl-C to stop)")
    print(HINT.format(root=a.root, version=a.version))
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

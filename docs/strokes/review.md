# Reviewing outlier swings (Phase 16.4a)

The drill blocks are trusted: a swing keeps its block's label unless the build puts it in `review` (a large count mismatch, lost
packets, a window past the recording's edge, or a swing far from the others of its class and side; `dataset.md` step 7). This local
tool lets you accept, relabel or discard those swings. Nothing leaves your machine: it binds 127.0.0.1 only and refuses any other
`Host`/`Origin`.

```bash
tools/.venv/bin/python tools/strokes/build_dataset.py            # makes processed/swings_v1.npz
tools/.venv/bin/python tools/strokes/review/server.py --reviewer R1 [--root DIR] [--version 1] [--port 8765]
# open http://127.0.0.1:8765/ ; Ctrl-C when done, then rebuild so the labels apply:
tools/.venv/bin/python tools/strokes/build_dataset.py
```

`--reviewer` is a pseudonymous ID (`R1`, `R2`, ...), never a name. Labels go to `ROOT/labels/<session>.csv`
(`swing_id,t_peak_us,class,side,source=review,reviewer`), one row per swing (a re-label replaces it), written atomically. The server
writes nothing else and only reads the npz. It does not rebuild: run `build_dataset.py` again.

## Keys
| key | action |
|---|---|
| `Enter` / `a` | accept the block label (keeps the side shown) |
| `x` | discard the swing |
| `1`-`9` | serve_short, serve_long, clear, smash, drop, drive, lift, net_shot, block_push |
| `0` / `-` | not_a_hit / net_kill |
| `f` / `b` | side FH / BH (labels at once with the picked class, else the block's) |
| `j` `k`, arrows | next / previous |
| `u` | undo the last label of this session |

A class key or `f`/`b` labels immediately and moves to the next unreviewed swing. The header shows "37 / 104 reviewed".

## What to look for
Red is |w| (dps), blue is |a| (g x100); dashed lines are the mean of the *labelled* swings of the same class and side; yellow
bands are saturated samples; the six small plots are the racquet-frame axes (wy is shaft roll). Pressing a class key re-draws the
comparison for that class before the next swing.
- A "smash" with a low, wide |w| and no shaft roll is usually a reset waggle: `not_a_hit` or discard.
- A very long build-up before the peak: the swing may be two actions in one window; discard if you can't tell.
- A "net_shot" or "drop" far stronger than its class: probably a clear or a drive.
- A swing whose peak is a sharp spike with no build-up (|a| huge, |w| small): a knock or a drop of the racquet, discard.
- Clipping is normal for hard smashes; it is not a reason to discard.

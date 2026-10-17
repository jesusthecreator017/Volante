# Collecting stroke data (operator guide)

One guided session per player: about 45 minutes on court. Result: a folder with `imu.bin` (the full 500 Hz recording) and
`session.json` (who, which pod, which block was when). Read [consent.md](consent.md) first, and [README.md](README.md) for what
each stroke means.

## Equipment
- The pod on the racquet (charged; fw with the FIFO framing guard from Phase 16.0 flashed), tight in its mount.
- A laptop with Bluetooth, `oso-rec` built (`cargo build --release -p oso-replay --bin oso-rec`), within a few metres of the court.
- A phone on a tripod for the optional video.
- Shuttles, a court, and the participant's own racquet if they prefer (move the pod over and **redo the mount calibration**).

## Before the session
1. **Mount calibration** (once per pod mounting, and again whenever the pod is moved): `oso-rec calibrate`. Hold the racquet
   upright, tip up, still, Enter; then lay it flat, strings up, still, Enter. It saves `~/.config/oso/mount.json`, which
   `collect` requires (it offers to run the calibration when the file is missing) and copies into `session.json`.
2. **Charge and wake:** `oso-rec status` shows `imu_ok`. A pod with `pending_upload` holds an un-uploaded session: upload it
   first (`oso-rec upload -o FILE`).
3. **Consent** and the participant's code (`P07`, the next free number; never a name). Note handedness, level and
   height range (5 cm bin, e.g. 170-175).
4. **Phone:** side-on to the player at mid-court height, 5 to 8 m away, whole body **and** racquet in frame the whole time, 60 fps,
   landscape. Start recording before the session; stop after it.

## Tap sync (video)
At the **start** and the **end**: stand still holding the racquet for 2 seconds, then **tap the racquet head on the floor or a
shoe three times**, clearly, in view of the camera. The taps show as spikes in the IMU and as a bang/visible impact in the video; they
align the two clocks. Do it again at the very end, so drift can be measured.

## What to tell the participant
- "A screen and the pod's light will tell you what to hit. Hit about 25 of each, with **2 seconds of stillness between swings**:
  hold the racquet still after each shot. That gap is how the swings are found."
- "Hit the way you normally would. Don't try to hit harder for the sensor."
- "Forehand and backhand are about your racquet hand, not the court side" (see README).
- "Say if you need a break; we pause."
- Left-handed players need no special handling: set `--handed L`.

## Run
```
oso-rec collect --player P07 --handed R --level club --height-cm-bin 170-175 [--out DIR] [--no-view] [--force]
```
Defaults: protocol `drills-v1` (`tools/strokes/protocols/`, compiled in; or give a file path), sessions under
`~/Datasets/oso-strokes/raw/`. A live racquet window opens (`--no-view` for terminal only). The window's title and the terminal
show the block `k/N`, the stroke and side, the counted reps against the target, and the time. `--force` overwrites an un-uploaded
session on the pod after a typed confirmation: never use it on a session you still need.

Order is shuffled per session (the seed is stored in `session.json`): warm-up first, free play last (timed, unlabelled).

| key (in the terminal) | action |
|---|---|
| `space` / `n` | end this block and start the next (the first press starts block 1) |
| `r` | redo: the attempt is marked `discarded` (data kept), the block starts again |
| `s` | skip the block |
| `p` | pause: ends the block, a break with no label; `space` starts the next |
| `q` (or Ctrl-C) | finish the session early |

The pod's LED glows at every block start and end (a `0x06` marker). The live counter (gyro peak above the block's threshold, 0.8 s apart) rings
the terminal bell and shows **DONE: press SPACE** at the target; it is a guide, the block's reps are re-counted offline, and soft
swings may be counted too, so the target is approximate. Let the player finish the swing, then press space.

The threshold is per block (`count_threshold_dps` in the protocol JSON, `0` = counter off, default 400). drills-v1 starting guesses,
to tune in the pilot: smash, clear, long serve 700; drive, lift 450; drop, block/push 300; net shot, short serve 200; net kill 350;
not_a_hit off. If a block over- or under-counts in the pilot, change the number in `tools/strokes/protocols/drills-v1.json`.

When the blocks are done: the recording stops, you may type a free-text note (no names), the session is downloaded from the pod
with the packet count checked, and the folder is printed. The pod is only told it is uploaded after a complete download.

## After the session
- Stop the phone video; save it as `video.mp4` **next to** the session folder's files (it is git-ignored; it never goes in the repo).
- Check `session.json`: the blocks, the counts (blocks that ran short or long get reviewed later), the markers.
- Add the participant to `private/participants.csv`.

## Troubleshooting
- **No OSO device found / pod asleep:** `oso-rec wake` (a sleeping pod only advertises for 2 s every 30 s; it takes up to 40 s).
- **"imu_ok clear":** the IMU did not start; power-cycle the pod.
- **The pod holds an un-uploaded session:** `oso-rec upload -o old.bin`, or `--force` if it is disposable.
- **Upload failed:** `collect` keeps the pod's session (it does not mark it uploaded) and prints
  `oso-rec upload -o <folder>/imu.bin`; run it. `session.json` was already written with the block times.
- **Link lost mid-session:** the pod keeps recording; the screen shows "reconnecting" and the counter pauses, then continues.
  Keys pressed meanwhile apply when the link is back.
- **Misaligned data warning / implausible rest (after the framing guard):** the stream's bytes were shifted. Reset the pod
  (power-cycle it), redo the mount check with `oso-rec live`, and re-record the session; QC rejects such sessions anyway.
- **Racquet doesn't move in the window / looks wrong:** the mount is stale; recalibrate (`oso-rec calibrate`).
- **Terminal garbled after a crash:** `stty sane`.

## Times in session.json
Block `t_start_us` / `t_end_us` are the **device clock**, the same clock as the packets in `imu.bin`, so they map onto the
recording directly. They are stamped when you press the key (to a few tens of ms); the `markers` list holds the pod's own marker
times, which are exact (each block start/end sends one).

## Repo safety
`.gitignore` blocks `*.mp4`, `*.mov` and session folders; `python tools/strokes/check_no_data.py` fails if such files are tracked
(it runs with `tools/strokes/test_collect_tools.py` in CI).

# The seven play styles

A style is a **build** (power / speed / reach / accuracy, each 1-10, summing to 24) plus a **reward** that pays for the shots that style plays.
Use the build as `--build P,S,R,A` and the file as `--reward <this folder>/rewards/<file>`. Each reward file also has a `BUILD` constant.

| style | build P/S/R/A | reward focus | expected weakness | counters (what beats it, expected) | file |
|---|---|---|---|---|---|
| Smasher / Attacker | 9/6/5/4 | points won with steep fast shots from above, towards the sidelines | defence, unforced errors (fast shots are noisy) | Wall, Counter-attacker | `rewards/smasher.py` |
| Wall / Defender | 3/8/8/5 | returns under pressure, deep high lifts and clears | few winners, slow to finish | Net Dominator, Controller | `rewards/wall.py` |
| Net Dominator | 3/6/7/8 | tight net shots, front-court kills | deep clears over the head | Wall, Speedster (clears and flat pushes) | `rewards/net.py` |
| Controller / Placer | 4/6/5/9 | landing far from the opponent, drop / clear mix, corners | no finishing power | Smasher (punishes soft replies), Speedster | `rewards/controller.py` |
| Speedster / Tempo | 4/9/5/6 | fast flat drives and pushes, taking the shuttle early | reach on wide smashes | Smasher, Wall | `rewards/tempo.py` |
| Counter-attacker | 6/7/7/4 | defend, then attack weak replies | accuracy | Controller, Net Dominator | `rewards/counter.py` |
| All-rounder | 6/6/6/6 | balanced (the default terms, capped) | no standout | any specialist that plays its style well | `rewards/allrounder.py` |

The weaknesses and counters are what the build numbers and the reward imply (a 9-accuracy build makes small errors, a 3-power build cannot finish); they are
**expectations to test, not measured results**. Play your model against the other styles with `oso-arena eval A.safetensors --vs B.safetensors`.

## Target metrics (what `oso-arena eval` should show once the style is achieved)

These are steering numbers derived from the shot classes in `oso_arena/evaluate.py` (smash: contact above 2.0 m, steeper than -5 degrees, faster than 25 m/s;
drop: soft and short; clear: high and deep; drive: flat at mid height; lift: from below 1.2 m, high; net: close to the net). They have **not** been measured on trained models yet:
take the numbers of your first complete run as the baseline and check that the style's reward moves them in the stated direction.

| style | shot mix should show | other |
|---|---|---|
| Smasher | the largest smash share of the seven styles, high mean launch speed | win rate vs bot above 60 % but more `out` / `net` faults than the All-rounder |
| Wall | many lift + clear, almost no smash | longest rallies (rally mean above the All-rounder's), few `missed` winners |
| Net Dominator | many net + drop | short mean displacement per shot, contact near the net (low `x_contact` shots) |
| Controller | drop + clear mix | the highest mean displacement `d` (m) and opponent run per shot |
| Speedster | the largest drive share, low mean apex | short rallies, fast launch speeds with a flat angle |
| Counter-attacker | lifts / clears early in the rally, smashes late | win rate vs bot above the Wall's with shorter rallies |
| All-rounder | a spread over clear, drop, smash, drive | no fault type above a few percent |

Always, for any style: serve faults per rally below 0.05, win rate against the bot above 50 % after a real training run (a smoke run proves nothing).

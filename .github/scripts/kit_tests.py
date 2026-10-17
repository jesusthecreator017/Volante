"""CI runner for the Arena kit tests: arena/kit/tests/run_all.py, then every test in arena/kit/oso_arena/test_badsim.py (run one by
one: its own main() stops at the first failure).

A few tests hash float outputs bit for bit (sha256 / tobytes) against goldens recorded on macOS arm64. On Linux x86-64 they differ in
the last ulp (2.9e-16), so those, and only those, may fail here: they become a ::warning. Any other failure fails the job."""
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_GOLDENS = {
    "test_rewards.test_default_reward_equals_mains_per_step_rewards",
    "test_rewards.test_golden_rollout_fingerprint_equals_mains",
    "test_rewards.test_match_logs_are_byte_identical_to_main",
    "test_sim_builds.test_all6_matches_the_golden_rollout",
    "test_badsim.test_synth_motion_unchanged",
}

failed = []

r = subprocess.run([sys.executable, str(ROOT / "arena/kit/tests/run_all.py")], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   text=True)
print(r.stdout, flush=True)
m = re.search(r"^\d+ tests, (\d+) failed(?:: (.*))?$", r.stdout, re.M)
if not m:
    print(f"::error title=Arena kit tests::run_all.py ended without a summary (exit {r.returncode})")
    sys.exit(1)
failed += [s.strip() for s in (m.group(2) or "").split(",") if s.strip()]
if r.returncode != 0 and not failed:
    print(f"::error title=Arena kit tests::run_all.py exited {r.returncode} with no failed test listed")
    sys.exit(1)

print("== test_badsim.py", flush=True)
sys.path.insert(0, str(ROOT / "arena/kit/oso_arena"))  # as when the file runs as a script
import test_badsim  # noqa: E402

tests = [(k, v) for k, v in sorted(vars(test_badsim).items()) if k.startswith("test_") and callable(v)]
for k, t in tests:
    try:
        t()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        failed.append(f"test_badsim.{k}")
        print(f"FAIL {k}", flush=True)
print(f"test_badsim: {len(tests)} tests run")

golden = sorted(set(failed) & PLATFORM_GOLDENS)
unexpected = sorted(set(failed) - PLATFORM_GOLDENS)
if golden:
    print(f"::warning title=Arena kit goldens::bit-exact goldens recorded on macOS differ on Linux: {', '.join(golden)}")
if unexpected:
    print(f"::error title=Arena kit tests::failed: {', '.join(unexpected)}")
    sys.exit(1)
print(f"kit ok ({len(golden)} platform goldens tolerated)")

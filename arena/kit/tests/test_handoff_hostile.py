"""Phase 14.6 QA: a hostile pickle named .safetensors is refused by `check` without ever being unpickled (a __reduce__ payload that would
create a marker file must not run), and the PROMPT.md type block names every field of site/src/types.ts."""
import pickle
import re
import struct
import tempfile
from pathlib import Path

from helpers import ROOT, run

from oso_arena import check


class _Evil:
    def __init__(self, marker):
        self.marker = marker

    def __reduce__(self):
        return (Path(self.marker).write_text, ("unpickled",))


def test_pickle_is_never_unpickled():
    with tempfile.TemporaryDirectory() as t:
        marker = Path(t) / "marker"
        blob = pickle.dumps(_Evil(str(marker)), protocol=2)
        plain = Path(t) / "plain.safetensors"
        plain.write_bytes(blob)
        # also with a small, plausible 8-byte header length in front, so the parser goes further than the length cap
        framed = Path(t) / "framed.safetensors"
        framed.write_bytes(struct.pack("<Q", 16) + b'{"a":1}' + b" " * 9 + blob)
        for f in (plain, framed):
            r = check.check_file(f)
            assert not r.ok and r.reasons and all("smoke" not in x for x in r.reasons), (f.name, r.reasons)
        assert not marker.exists(), "the pickle payload ran"


def test_prompt_names_every_type_field():
    prompt = (ROOT / "arena" / "handoff" / "PROMPT.md").read_text(encoding="utf-8")
    block = prompt[prompt.index("### Types (docs/site-types.ts)"):prompt.index("### Endpoints")]
    types = (ROOT / "site" / "src" / "types.ts").read_text(encoding="utf-8")
    missing = []
    for m in re.finditer(r"export interface (\w+)(?: extends \w+)? \{(.*?)\n\}", types, re.S):
        for f in re.findall(r"^\s+(\w+)\??:", m.group(2), re.M):
            if not re.search(r"\b" + f + r"\b", block):
                missing.append(f"{m.group(1)}.{f}")
    assert not missing, missing


if __name__ == "__main__":
    run(globals())

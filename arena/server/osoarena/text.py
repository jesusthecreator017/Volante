"""User text is stored as plain text: control/format characters stripped, length-capped. Never interpreted, never put in HTML."""
import json
import re
import unicodedata

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def clean(s, cap, allow_newline=False):
    if not isinstance(s, str):
        return None
    out = []
    for ch in s:
        if allow_newline and ch == "\n":
            out.append(ch)
        elif unicodedata.category(ch)[0] == "C":
            continue
        else:
            out.append(ch)
    s = "".join(out).strip()
    return s[:cap] if s else None


def valid_build(b):
    keys = ("power", "speed", "reach", "accuracy")
    if not isinstance(b, dict) or set(b) != set(keys):
        return None
    if not all(type(b[k]) is int and 1 <= b[k] <= 10 for k in keys) or sum(b[k] for k in keys) != 24:
        return None
    return {k: b[k] for k in keys}


def safe_login(s):
    return s if isinstance(s, str) and LOGIN_RE.match(s) else "user"


def dumps(o):
    return json.dumps(o, separators=(",", ":"))

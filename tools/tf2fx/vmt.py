"""Minimal Source VMT reader: shader name + lowercase parameter dict."""
import re

_TOKEN = re.compile(r'"([^"]*)"|([^\s{}"]+)|([{}])')


def parse(text):
    lines = []
    for line in text.replace("\r", "").split("\n"):
        i = line.find("//")
        lines.append(line if i < 0 else line[:i])
    toks = []
    for m in _TOKEN.finditer("\n".join(lines)):
        toks.append(m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3)))
    if not toks:
        return None, {}
    shader = toks[0].lower()
    params, i, depth = {}, 1, 0
    while i < len(toks):
        t = toks[i]
        if t == "{":
            depth += 1
            i += 1
            continue
        if t == "}":
            depth -= 1
            i += 1
            continue
        if depth == 1 and i + 1 < len(toks) and toks[i + 1] not in ("{", "}"):
            params[t.lower()] = toks[i + 1]
            i += 2
            continue
        i += 1
    return shader, params


def load(path):
    return parse(open(path, "rb").read().decode("latin-1"))


def number(params, key, default=0.0):
    v = params.get(key)
    if v is None:
        return default
    try:
        return float(v.strip("[] "))
    except ValueError:
        return default

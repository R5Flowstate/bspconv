"""Find the S21 shader set that is the exact twin of each TF2 shader set.

TF2 and S21 name uber permutations with the same feature-token grammar; S21
adds fixed tokens (TnBnInterp, Uv0m0m0Interp, VnrmNoInterp, a PS prefix on the
sampler list) and renames a few. Candidates are generated from the TF2 name and
accepted only when StringToGuid of the candidate path is a shader set that
exists in a stock S21 pak, so a hit is proven by the hash, not by the rules.
"""
import csv
import itertools
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
from guid import string_to_guid  # noqa: E402

TYPE_MAP = {"wld": ("wldc", "wldu"), "fix": ("rgdp", "rgdu", "rgdc"), "skn": ("sknp", "sknu", "sknc"),
            "rgd": ("rgdp", "rgdu", "rgdc")}

# TF2 token -> S21 spellings to try (first is preferred).
ALIASES = {
    "Emit": ["Emit", "Emi"],
    "Emul": ["Emul", "Eml"],
    "Opam": ["Opam", "Opm"],
    "Entcolme": ["Entcolme", "Entcolmd"],
}

PREFIX_TOKENS = ("Unlit", "Vcoltvcola", "Vcolt", "Vcolao", "Vcola", "Lyrtrans", "Lyr", "Depth", "Vsm")


def s21_guids(list_dir):
    out = {}
    for f in os.listdir(list_dir):
        for r in csv.reader(open(os.path.join(list_dir, f), encoding="utf-8", errors="replace")):
            if r and r[0] == "shds":
                out[int(r[3], 16)] = r[5]
    return out


def tokenize(body):
    return re.findall(r"[A-Z][a-z0-9]*|[0-9]+", body)


UV_TAIL = re.compile(r"((?:Uvd\d)*)((?:Uv\d[a-z]*)*)(UV\d+)?$")


def split_name(name):
    m = re.match(r"shaderset[\\/](uber)(.*)_([a-z]+)\.rpak$", name)
    if not m:
        return None
    body, typ = m.group(2), m.group(3)
    samp = re.search(r"Samp([0-9]+)$", body)
    digits = samp.group(1) if samp else None
    if samp:
        body = body[:samp.start()]
    uv = UV_TAIL.search(body)
    head = body[:uv.start()] if uv else body
    uvd = re.findall(r"Uvd(\d)", uv.group(1)) if uv else []
    sets = re.findall(r"Uv(\d)([a-z]*)", uv.group(2)) if uv else []
    uvdecl = uv.group(3) if uv else None
    return tokenize(head), digits, typ, (uvd, sets, uvdecl)


def uv_segments(uvinfo):
    """S21 UV segment spellings for a TF2 UV block (tried in order)."""
    uvd, sets, uvdecl = uvinfo
    n = int(uvdecl[2]) if uvdecl else 1
    tail = uvdecl[3:] if uvdecl else ""
    mods = {}
    for k, m in sets:
        mods.setdefault(int(k), set()).update(re.findall(r"at|d|s", m))
    def entry(k):
        md = mods.get(k, set())
        if "s" in md:
            return "Uv%dst" % k if "at" in md else "Uv%ds" % k
        suffix = ("d" if "d" in md else "") + ("t" if "at" in md else "")
        return "Uv%dm0%s" % (k, suffix)
    tails = [tail]
    for i in range(len(tail) + 1):
        for c in "0123":
            tails.append(tail[:i] + c + tail[i:])
    out = []
    pre = "".join("Uvd%sPs" % k for k in uvd)
    for include0 in (True, False):
        ents = [entry(k) for k in range(n) if include0 or k != 0 or mods.get(0)]
        body = pre + "".join(ents) + "m0Interp"
        if not uvdecl:
            out.append(body)
            continue
        for t in tails:
            out.append(body + "UV%d%s" % (n, t))
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def candidates(tokens, digits, typ, uvinfo=((), (), None)):
    """Yield (candidate, aliased) with exact TF2 spellings first."""
    lead = []
    rest = list(tokens)
    while rest and rest[0] in ("Unlit", "Vcolt", "Vcola", "Vcolao", "Lyr", "Lyrtrans", "Depth", "Vsm"):
        lead.append(rest.pop(0))
    depth = "Depth" in lead
    unlit = "Unlit" in lead
    options = [ALIASES.get(t, [t]) for t in rest]
    segs = uv_segments(uvinfo)
    seen = set()
    for combo in itertools.product(*options) if options else [()]:
        aliased = any(c != t for c, t in zip(combo, rest))
        feats = list(combo)
        extra = [[]]
        if depth and typ == "skn":
            extra = [["Vbwe"], []]
        for ex in extra:
            for perm in itertools.permutations(feats + ex) if len(feats) + len(ex) <= 7 else [feats + ex]:
                mid = "".join(perm)
                for s21_type in TYPE_MAP.get(typ, (typ,)):
                    for opt in ("", "OptLow"):
                        head = "".join(lead)
                        samp = "PSSamp%s" % digits if digits else ""
                        for seg in segs:
                            if depth:
                                names = ["uber%sVnrmNoInterp%s%s%s_%s" % (head, mid, opt, seg, s21_type),
                                         "uber%sVnrmNoInterp%s%s_%s" % (head, mid, opt, s21_type)]
                            elif unlit:
                                names = ["uber%sVnrmNoInterp%s%s%s%s_%s" % (head, mid, opt, seg, samp, s21_type),
                                         "uber%s%s%s%s%s_%s" % (head, mid, opt, seg, samp, s21_type)]
                            else:
                                names = ["uber%sTnBnInterp%s%s%s%s_%s" % (head, mid, opt, seg, samp, s21_type)]
                            for nm in names:
                                if nm not in seen:
                                    seen.add(nm)
                                    yield nm, aliased


def main(argv):
    s21 = s21_guids(argv[0])
    tf2_names = [l.strip() for l in open(argv[1], encoding="utf-8") if l.strip()]
    hit = miss = 0
    for n in tf2_names:
        parts = split_name(n)
        found = None
        if parts:
            for c, aliased in candidates(*parts):
                g = string_to_guid("shaderset/%s.rpak" % c)
                if g in s21:
                    found = (c + (" [ALIAS]" if aliased else ""), g)
                    break
        if found:
            hit += 1
            print("TWIN  %-70s -> %s 0x%016X" % (n, found[0], found[1]))
        else:
            miss += 1
            print("NONE  %s" % n)
    print("twins %d / %d" % (hit, hit + miss))


if __name__ == "__main__":
    main(sys.argv[1:])

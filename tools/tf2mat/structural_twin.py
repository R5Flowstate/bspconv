"""Rank S21 shader-set candidates for a TF2 set that has no name twin.

    py structural_twin.py <config.json> <tf2 shaderset name> [...]

A candidate must bind the same textures, in the same slots, in its pixel
shader as the TF2 one (RDEF resource bindings); survivors are ranked by the
feature tokens the two names share once the S21-only fixed tokens are
removed. Output is for review: the chosen twin goes into the twin override
table with the evidence printed here.
"""
import json
import re
import sys

import dxbc_rdef
import tf2_matl_to_s21 as t

FIXED = ("TnBnInterp", "VnrmNoInterp", "Uv0m0m0Interp", "m0Interp", "OptLow", "Interp")
ALIAS = {"Emit": "Emi", "Opam": "Opm", "Emul": "Eml"}


def feature_tokens(name):
    stem = re.sub(r"^.*[\\/]", "", name)
    stem = re.sub(r"\.rpak$", "", stem)
    stem = re.sub(r"_[a-z]+$", "", stem)
    stem = re.sub(r"(PS|VS)?Samp[0-9]+", "", stem)
    stem = stem[len("uber"):] if stem.startswith("uber") else stem
    for f in FIXED:
        stem = stem.replace(f, "")
    toks = re.findall(r"[A-Z][a-z0-9]*", stem)
    return {ALIAS.get(x, x) for x in toks if not re.match(r"^(Uv|UV)\d", x)}


ENGINE_TEXTURES = re.compile(r"^(IndirectSpecular|CSM|t_shadowMaps|cloudMask|lightmap|s_realTime|g_|t_|s_)", re.I)


def texture_binds(shaders, ps_guid):
    p = shaders.path.get(ps_guid)
    if not p:
        return None
    data = open(p, "rb").read()
    for b in dxbc_rdef.blobs(data):
        r = dxbc_rdef.rdef_of(b)
        if r:
            return tuple((n, pt) for n, typ, pt, c in r[0] if typ == 2 and not ENGINE_TEXTURES.match(n))
    return None


def main(argv):
    cfg = json.load(open(argv[0]))
    c = t.Converter(cfg)
    by_name = {v.lower(): k for k, v in c.tf2_set_names.items()}
    s21_named = {}
    set_type = {}
    for gset, mats in c.s21_by_set.items():
        for m in mats:
            set_type.setdefault(gset, m.get("shaderType"))
    for gid, name in c.s21_sets.items():
        if re.search(r"[\/]0x[0-9A-Fa-f]+\.rpak$", name) and gid in set_type:
            name = name[:-5] + "_" + set_type[gid] + ".rpak"
        s21_named[gid] = name
    for want in argv[1:]:
        tf2_gid = by_name.get(want.lower())
        if tf2_gid is None:
            print("unknown TF2 set", want)
            continue
        typ = re.search(r"_([a-z]+)\.rpak$", want).group(1)
        s21_types = t.shaderset_twin.TYPE_MAP.get(typ, (typ,))
        tf2_binds = texture_binds(c.shaders, c.shaders.set_ps(tf2_gid))
        tf2_feats = feature_tokens(want)
        print("==", want)
        print("   TF2 textures:", [n for n, _ in tf2_binds or ()])
        ranked = []
        for gid, name in s21_named.items():
            if not re.search(r"_(%s)\.rpak$" % "|".join(s21_types), name):
                continue
            ps = c.shaders.set_ps(gid)
            binds = texture_binds(c.shaders, ps) if ps else None
            if binds is None or tf2_binds is None:
                continue
            if [n for n, _ in binds] != [n for n, _ in tf2_binds]:
                continue
            f = feature_tokens(name)
            score = len(f & tf2_feats) - len(f ^ tf2_feats) * 0.5
            ranked.append((score, name, gid, sorted(f - tf2_feats), sorted(tf2_feats - f)))
        ranked.sort(reverse=True)
        if not ranked:
            print("   no S21 set binds the same textures")
        for score, name, gid, extra, miss in ranked[:4]:
            print("   %5.1f 0x%016X %s  +%s -%s" % (score, gid, name, extra, miss))


if __name__ == "__main__":
    main(sys.argv[1:])

"""TF2 particle VMTs -> S21 particle materials (matl v23 ptcu/ptcs) as Repak docs.

    py particle_mats.py <mat_corpus.pkl> <vmt root> <out assets dir> <material>:<ptcu|ptcs> ...

Calibrated on the TF2 particle VMTs S21 still ships under the same name
(mat_corpus.py). For a material S21 lacks:
  1. twin = an S21 material of the same VMT shader whose STRUCTURAL flags
     (the ones that pick shader set, blend and depth state) equal this VMT's,
     preferring the most identical scalars. Its header, shader set, textures
     and uber are the starting point -- they are S21's own bake of the same
     shader configuration.
  2. every VMT scalar with a proven uber slot is written from this VMT
     (slots are re-proven against the whole corpus on every run).
  3. textures come from this VMT's own texture names.
  4. $ignorez sets depthStencilFlags (0x0 / 0x7), proven on every pair.
  5. ptcs is the ptcu result with the S21 ptcu->ptcs shader-set map and
     uber dwords 0/1 = 1.0, the only differences across all 106 S21
     materials that ship both variants.
"""
import collections
import json
import os
import pickle
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))

import vmt  # noqa: E402
from guid import string_to_guid  # noqa: E402

# VMT keys that select shader set / blend / depth-stencil state in S21.
STRUCTURAL = ("$additive", "$depthblend", "$nowritez", "$alphatest", "$disabletsaa",
              "$disabledof", "$blendframes", "$dualsequence", "$orientation", "$opaque",
              "$forcerefract", "$translucent", "$vertexcolor", "$vertexalpha", "$addbasetexture2",
              "$addoverblend", "$addself", "$overbrightfactor", "$perparticleoutline",
              "$minsize", "$maxsize", "$distancealpha", "$softedges", "$outline", "$mod2x")
TEXTURE_KEYS = ("$basetexture", "$normalmap", "$opacitymap", "$dudvmap", "$basetexture2")


def truthy(v):
    if v is None:
        return False
    try:
        return float(str(v).strip("[] ")) != 0.0
    except ValueError:
        return True


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


# Uber float slots per (VMT shader, shader type): key -> (slot, transform).
SLOTS = {
    ("spritecard", "ptcu"): {
        "$startfadesize": (2, lambda v: v),
        "$endfadesize": (3, lambda v: v),
        "$depthblendscale": (8, lambda v: 1.0 / v if v else 0.0),
    },
    ("refract", "ptcu"): {
        "$refractamount": (15, lambda v: v),
    },
}


def prove_slots(corpus):
    """Hit rate of every uber slot rule over the calibration pairs; a rule under
    95% is dropped (the misses that remain are materials Apex retuned)."""
    proven = {}
    for (shader, st), rules in SLOTS.items():
        keep = {}
        for key, (slot, fn) in rules.items():
            hit = tot = 0
            for n, t, g in corpus["pairs"]:
                sh, p = corpus["vmts"][n]
                if sh != shader or t != st or key not in p:
                    continue
                if key == "$depthblendscale" and not truthy(p.get("$depthblend")):
                    continue
                uber = corpus["mats"][g][1]
                if 4 * slot + 4 > len(uber):
                    continue
                tot += 1
                want = f32(fn(vmt.number(p, key)))
                got = struct.unpack_from("<f", uber, 4 * slot)[0]
                hit += abs(want - got) <= 1e-6 * max(1.0, abs(want))
            rate = hit / tot if tot else 0.0
            print("  slot %-18s %s/%s -> uber[%d]: %d/%d (%.1f%%)" % (key, shader, st, slot, hit, tot, 100 * rate))
            if tot >= 3 and rate >= 0.95:
                keep[key] = (slot, fn)
        proven[(shader, st)] = keep
    return proven


def ptcs_rule(corpus):
    by = collections.defaultdict(dict)
    for g, (doc, uber, name, st) in corpus["mats"].items():
        by[name.lower()][st] = (doc, uber)
    shds = {}
    d01 = collections.Counter()
    for v in by.values():
        if "ptcu" in v and "ptcs" in v:
            a, ua = v["ptcu"]
            b, ub = v["ptcs"]
            prev = shds.setdefault(a["shaderSet"], b["shaderSet"])
            if prev != b["shaderSet"]:
                raise ValueError("ptcu shader set %s maps to two ptcs sets" % a["shaderSet"])
            d01[ub[:8]] += 1
    (d01_bytes, n), = d01.most_common(1)
    if n != sum(d01.values()):
        raise ValueError("ptcs uber dwords 0/1 are not constant")
    return shds, d01_bytes


def texture_guid(name):
    n = name.replace("\\", "/").lower()
    if n.endswith(".vtf"):
        n = n[:-4]
    return string_to_guid("texture/%s.rpak" % n)


def slot_defaults(corpus, slots):
    """Modal uber value of each slot over the pairs whose VMT omits that key."""
    out = {}
    for (shader, st), rules in slots.items():
        for key, (slot, fn) in rules.items():
            c = collections.Counter()
            for n, t, g in corpus["pairs"]:
                sh, p = corpus["vmts"][n]
                if sh == shader and t == st and key not in p:
                    c[corpus["mats"][g][1][4 * slot:4 * slot + 4]] += 1
            if c:
                out[(shader, st, key)] = c.most_common(1)[0][0]
    return out


def pick_twin(corpus, shader, types, params):
    want = {k: truthy(params.get(k)) for k in STRUCTURAL}
    best = None
    for n, t, g in corpus["pairs"]:
        sh, p = corpus["vmts"][n]
        if sh != shader or t not in types:
            continue
        if any(truthy(p.get(k)) != want[k] for k in STRUCTURAL):
            continue
        same = sum(1 for k, v in params.items() if p.get(k) == v)
        score = (same, -len(set(p) ^ set(params)))
        if best is None or score > best[0]:
            best = (score, n, g)
    return best


def convert(corpus, vmt_root, material, st, slots, defaults, shds_map, d01):
    path = os.path.join(vmt_root, "materials", material.replace("/", os.sep) + ".vmt")
    shader, params = vmt.load(path)
    # A ptcs material is its ptcu bake plus the proven ptcs rule, so the closest
    # VMT of either type is the twin.
    twin = pick_twin(corpus, shader, ("ptcu", "ptcs") if st == "ptcs" else (st,), params)
    if twin is None:
        raise KeyError("%s: no S21 twin with the same structural flags" % material)
    (_, tname, tg) = twin
    doc, uber, _, tst = corpus["mats"][tg]
    doc = dict(doc)
    uber = bytearray(uber)
    if st == "ptcs" and tst == "ptcs":
        raise NotImplementedError("%s: ptcs twin has no proven uber slots; use a ptcu twin" % material)
    for key, (slot, fn) in slots.get((shader, "ptcu"), {}).items():
        if key == "$depthblendscale" and not truthy(params.get("$depthblend")):
            continue
        if key in params:
            struct.pack_into("<f", uber, 4 * slot, fn(vmt.number(params, key)))
        elif (shader, "ptcu", key) in defaults:
            uber[4 * slot:4 * slot + 4] = defaults[(shader, "ptcu", key)]
    # $ignorez is the depth test: depthStencilFlags 0x0 with it, 0x7 without (305/305 pairs).
    doc["depthStencilFlags"] = "0x0" if truthy(params.get("$ignorez")) else "0x7"
    twin_params = corpus["vmts"][tname][1]
    tex = dict(doc["$textures"])
    for key in TEXTURE_KEYS:
        if key in params and key in twin_params:
            old = "0x%016X" % texture_guid(twin_params[key])
            new = "0x%016X" % texture_guid(params[key])
            for i, g in tex.items():
                if g.upper() == old.upper():
                    tex[i] = new
    doc["$textures"] = tex
    if st == "ptcs" and tst == "ptcu":
        if doc["shaderSet"] not in shds_map:
            raise KeyError("%s: twin shader set %s has no ptcs twin" % (material, doc["shaderSet"]))
        doc["shaderSet"] = shds_map[doc["shaderSet"]]
        doc["shaderType"] = "ptcs"
        uber[0:8] = d01
    doc["name"] = material
    return doc, bytes(uber), {"material": material, "type": st, "twin": "%s_%s" % (tname, tst),
                              "vmtShader": shader}


def main(argv):
    corpus = pickle.load(open(argv[0], "rb"))
    vmt_root, out = argv[1], argv[2]
    print("proving uber slots:")
    slots = prove_slots(corpus)
    defaults = slot_defaults(corpus, slots)
    shds_map, d01 = ptcs_rule(corpus)
    print("ptcu->ptcs shader sets", len(shds_map), "uber d0/d1", d01.hex())
    report = []
    for spec in argv[3:]:
        material, st = spec.rsplit(":", 1)
        doc, uber, info = convert(corpus, vmt_root, material, st, slots, defaults, shds_map, d01)
        rel = "material/%s_%s" % (material, st)
        d = os.path.join(out, os.path.dirname(rel))
        os.makedirs(d, exist_ok=True)
        json.dump(doc, open(os.path.join(out, rel + ".json"), "w"), indent=1)
        open(os.path.join(out, rel + ".uber"), "wb").write(uber)
        info["guid"] = "0x%016X" % string_to_guid(rel + ".rpak")
        info["textures"] = doc["$textures"]
        report.append(info)
        print(json.dumps(info))
    json.dump(report, open(os.path.join(out, "_particle_mats.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])

"""Pair TF2 particle VMTs with the S21 particle materials that carry the same
name, the calibration set for the VMT -> matl conversion.

    py mat_corpus.py <vmt root (contains materials/)> <out.pkl> <s21 dec paks...>
"""
import collections
import glob
import os
import pickle
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
import rpak  # noqa: E402
from matl_export import s21_to_repak  # noqa: E402

import vmt  # noqa: E402


def texture_slots(pk, a):
    """Every texture bind point: the permanent block at +0x60 and the streamed block
    at +0x68 are parallel arrays of the shader set's slot count; a streamed texture
    sits in the second with a null in the first."""
    hdr = pk.read(a.head, 256)
    p60 = struct.unpack_from("<ii", hdr, 0x60)
    p68 = struct.unpack_from("<ii", hdr, 0x68)
    if p60 == (0, 0) or p68[0] != p60[0] or p68[1] < p60[1]:
        return {}, 0
    n = (p68[1] - p60[1]) // 8
    perm = struct.unpack("<%dQ" % n, pk.read(rpak.PagePtr(*p60), 8 * n)) if n else ()
    strm = struct.unpack("<%dQ" % n, pk.read(rpak.PagePtr(*p68), 8 * n)) if n else ()
    slots = {}
    for i in range(n):
        g = perm[i] or strm[i]
        if g:
            slots[str(i)] = "0x%016X" % g
    return slots, n


def main(root, out, paks):
    base = os.path.join(root, "materials")
    vmts = {}
    for p in glob.glob(os.path.join(base, "particle", "**", "*.vmt"), recursive=True):
        n = os.path.relpath(p, base)[:-4].replace(os.sep, "/").lower()
        vmts[n] = vmt.load(p)
    mats = {}
    for pp in paks:
        pk = rpak.open_pak(pp)
        for a in pk.by_type("matl"):
            if a.guid in mats:
                continue
            try:
                doc, uber, name, st = s21_to_repak(pk, a)
            except Exception:
                continue
            if name.lower().startswith("particle/"):
                doc["$textures"], doc["textureSlotCount"] = texture_slots(pk, a)
                mats[a.guid] = (doc, uber, name, st)
    pairs = [(name.lower(), st, g) for g, (doc, uber, name, st) in mats.items() if name.lower() in vmts]
    pickle.dump({"vmts": vmts, "mats": mats, "pairs": pairs}, open(out, "wb"))
    print("tf2 vmts", len(vmts), "s21 particle mats", len(mats), "pairs", len(pairs))
    print(collections.Counter((vmts[n][0], st) for n, st, g in pairs).most_common())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])

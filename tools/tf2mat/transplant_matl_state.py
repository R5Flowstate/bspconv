"""Restore material header fields the RSX export drops on transplanted S21 materials.

    py transplant_matl_state.py <transplant dir> <s21 lists dir> <s21 decoded paks dir>

RSX writes unk_CC (+0xCC, the foliage wind class) as 0, and Repak packs what the
json says, so a transplanted wind material would ship without its wind class.
The value is read from the material's header in the S21 pak it came from.
"""
import csv
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
from guid import string_to_guid  # noqa: E402
import rpak  # noqa: E402

FIELDS = {"unk_CC": (0xCC, "<I")}


def main(argv):
    tdir, lists, paks = argv
    where = {}
    for f in os.listdir(lists):
        for r in csv.reader(open(os.path.join(lists, f), encoding="utf-8", errors="replace")):
            if r and r[0] == "matl":
                where.setdefault(int(r[3], 16), f[:-4])
    loaded, changed, missing = {}, 0, 0
    root = os.path.join(tdir, "material")
    for r, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".json"):
                continue
            p = os.path.join(r, f)
            rel = os.path.relpath(p, tdir).replace("\\", "/")[:-5] + ".rpak"
            gid = string_to_guid(rel)
            pak = where.get(gid)
            path = os.path.join(paks, "%s.rpak.dec.rpak" % pak) if pak else None
            if not path or not os.path.isfile(path):
                missing += 1
                continue
            if pak not in loaded:
                pk = rpak.Pak(path)
                loaded[pak] = (pk, {a.guid: a for a in pk.by_type("matl")})
            pk, matls = loaded[pak]
            a = matls.get(gid)
            if a is None:
                missing += 1
                continue
            head = pk.read(a.head, a.header_size)
            j = json.load(open(p, encoding="utf-8"))
            dirty = False
            for key, (ofs, fmt) in FIELDS.items():
                v = "0x%X" % struct.unpack_from(fmt, head, ofs)[0]
                if str(j.get(key)).upper() != v.upper():
                    j[key] = v
                    dirty = True
            if dirty:
                json.dump(j, open(p, "w", encoding="utf-8"), indent=1)
                changed += 1
    print("materials updated %d, without a source header %d" % (changed, missing))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

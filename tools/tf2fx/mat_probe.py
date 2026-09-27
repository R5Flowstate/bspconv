"""Print calibration pairs for one VMT shader / S21 shader type."""
import pickle
import struct
import sys

d = pickle.load(open(sys.argv[1], "rb"))
shader, st = sys.argv[2], sys.argv[3]
names = sys.argv[4:]
IGN = {"$basetexture", "$normalmap", "$opacitymap", "$dudvmap", "$texture2"}
for n, t, g in sorted(d["pairs"]):
    sh, p = d["vmts"][n]
    if sh != shader or t != st:
        continue
    if names and not any(x in n for x in names):
        continue
    doc, uber, name, _ = d["mats"][g]
    fl = struct.unpack("<%df" % (len(uber) // 4), uber)
    print("== %s  shds=%s mask=%s ds=%s rast=%s blend0=%s tex=%d feat=%s g1=%s g2=%s" % (
        n, doc["shaderSet"], doc["blendStateMask"], doc["depthStencilFlags"], doc["rasterizerFlags"],
        doc["blendStates"][0], len(doc["$textures"]), doc["features"], doc["glueFlags"], doc["glueFlags2"]))
    print("   vmt", {k: v for k, v in p.items()})
    print("   uber", " ".join("%g" % x for x in fl))

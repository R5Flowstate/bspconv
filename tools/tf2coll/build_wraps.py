"""Assemble a converted v51 client BSP into the S21 _client_perm/_client_temp wrap paks.

    py build_wraps.py <map> <converted dir> <work dir> [repak.exe]

Lump -> pak assignment is the one every S21 map ships (measured on district and
aqueduct): render/geometry in perm, entities/vis/cubemaps/game lump in temp.
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
import paths  # noqa: E402

PERM = [0x01, 0x03, 0x05, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x1E, 0x25, 0x26, 0x27, 0x47,
        0x49, 0x4A, 0x4F, 0x61, 0x62, 0x63, 0x64, 0x65, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C,
        0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7C,
        0x7E, 0x7F]
TEMP = [0x00, 0x02, 0x04, 0x0E, 0x18, 0x23, 0x2A, 0x2B, 0x36, 0x50, 0x51, 0x52, 0x53, 0x55,
        0x66, 0x7A, 0x7B, 0x7D]
ENTS = ["env", "fx", "script", "snd", "spawn"]


def main(argv):
    name, conv, work = argv[0], argv[1], argv[2]
    repak = argv[3] if len(argv) > 3 else paths.get("repakExe")
    present = {int(f.split(".bsp.")[1][:4], 16) for f in os.listdir(conv) if f.endswith(".bsp_lump")}
    unassigned = sorted(present - set(PERM) - set(TEMP))
    if unassigned:
        print("unassigned lumps: %s" % ["%02X" % x for x in unassigned])
        return 1
    out = os.path.join(work, "paks", "Win64")
    os.makedirs(out, exist_ok=True)
    for label, lumps in (("perm", PERM), ("temp", TEMP)):
        dst = os.path.join(work, label, "maps")
        shutil.rmtree(os.path.join(work, label), ignore_errors=True)
        os.makedirs(dst)
        files = []
        if label == "perm":
            shutil.copy2(os.path.join(conv, name + ".bsp"), dst)
            files.append("maps\\%s.bsp" % name)
        for lump in lumps:
            if lump not in present:
                continue
            leaf = "%s.bsp.%04X.bsp_lump.client" % (name, lump)
            shutil.copy2(os.path.join(conv, "%s.bsp.%04x.bsp_lump" % (name, lump)), os.path.join(dst, leaf))
            files.append("maps\\" + leaf)
        if label == "temp":
            for e in ENTS:
                leaf = "%s_%s.ent" % (name, e)
                if os.path.isfile(os.path.join(conv, leaf)):
                    shutil.copy2(os.path.join(conv, leaf), dst)
                    files.append("maps\\" + leaf)
        m = {"version": 8, "keepDevOnly": False, "name": "%s_client_%s" % (name, label),
             "assetsDir": os.path.join(work, label).replace("\\", "/") + "/",
             "outputDir": out.replace("\\", "/") + "/",
             "compressLevel": 0, "compressWorkers": 8, "headerFlags": 32,
             "files": [{"_type": "wrap", "_path": f} for f in files]}
        mp = os.path.join(work, "%s_client_%s.json" % (name, label))
        json.dump(m, open(mp, "w"), indent=1)
        r = subprocess.run([repak, mp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        print("%s: %d assets, repak rc=%d" % (label, len(files), r.returncode))
        if r.returncode:
            print(r.stdout[-2000:])
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

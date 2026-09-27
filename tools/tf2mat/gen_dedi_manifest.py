"""Repak manifest for a converted Titanfall 2 map's S3 dedicated-server pak.

    py gen_dedi_manifest.py <config.json>

Server scope only: v54 models (+ phy), plus the S3 rig and sequences of any model
converted with animations. Models whose guid the dedi's own paks already carry
are left to the resident original.
"""
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guid import string_to_guid  # noqa: E402
from gen_client_manifest import anim_entries, stage  # noqa: E402


def main(argv):
    cfg = json.load(open(argv[0]))
    resident = set()
    for f in os.listdir(cfg["dedi_lists"]):
        for r in csv.reader(open(os.path.join(cfg["dedi_lists"], f), encoding="utf-8", errors="replace")):
            if r and r[0] != "type":
                resident.add(int(r[3], 16))
    assets = cfg["assets_dir"]
    shutil.rmtree(assets, ignore_errors=True)
    order = json.load(open(cfg["anim_order"])) if cfg.get("anim_order") else {}
    files, anims, skipped = [], [], []
    for root, _, fs in os.walk(cfg["models"]):
        for f in sorted(fs):
            if not f.endswith(".rmdl"):
                continue
            src = os.path.join(root, f)
            path = "mdl/" + os.path.relpath(src, cfg["models"]).replace("\\", "/")
            gid = string_to_guid(path)
            if gid in resident:
                skipped.append(path)
                continue
            for ext in (".rmdl", ".phy"):
                if os.path.isfile(src[:-5] + ext):
                    stage(src[:-5] + ext, os.path.join(assets, path[:-5] + ext))
            entry = {"_type": "mdl_", "_path": path, "$guid": gid}
            rig, a = anim_entries(cfg["anim_dir"], order, path, assets) if order else (None, [])
            if rig:
                entry["$animrigs"] = [rig]
                anims += a
            files.append(entry)
    m = {"version": 8, "name": cfg["name"], "dedi": True, "keepClientOnly": False,
         "assetsDir": assets.rstrip("/") + "/", "outputDir": cfg["out_dir"].rstrip("/") + "/",
         "compressLevel": 6, "headerFlags": 32, "files": files + anims}
    json.dump(m, open(cfg["manifest"], "w"), indent=1)
    print("models %d (%d dedi-resident skipped), anim assets %d" % (len(files), len(skipped), len(anims)))


if __name__ == "__main__":
    main(sys.argv[1:])

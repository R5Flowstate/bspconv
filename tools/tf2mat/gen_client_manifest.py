"""Repak manifest for a converted Titanfall 2 map's client asset pak.

    py gen_client_manifest.py <config.json>

Stages models, converted materials and TF2 textures under one assetsDir and
writes the manifest models-first (Repak assigns segments in first-use order).
Every guid the pak references is then checked against itself plus the S21
resident universe (common*, startup*, ui); anything else that exists in an S21
map pak is transplanted from that original (same-guid law), and anything that
exists nowhere is reported -- the pak must not reference it.
"""
import csv
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
from guid import string_to_guid  # noqa: E402
from synth_v10_tail import synth_v10_tail, build_slot_map  # noqa: E402
from dds import read_dds_meta  # noqa: E402

RESIDENT_PREFIX = ("common", "startup", "ui")


def load_lists(list_dir):
    resident, where = set(), {}
    for f in os.listdir(list_dir):
        pak = f[:-4]
        for r in csv.reader(open(os.path.join(list_dir, f), encoding="utf-8", errors="replace")):
            if not r or r[0] == "type":
                continue
            gid = int(r[3], 16)
            if pak.startswith(RESIDENT_PREFIX):
                resident.add(gid)
            else:
                where.setdefault(gid, (r[0], pak, r[5]))
    return resident, where


def stage(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)


def tex_guid(t):
    stem = t.replace("\\", "/").split("/")[-1]
    if stem.startswith("0x"):
        return int(stem.split(".")[0], 16)
    return string_to_guid(t)


def material_refs(j):
    refs = [j.get("shaderSet", "0x0")]
    refs += [j.get(k, "0x0") for k in ("$depthShadowMaterial", "$depthPrepassMaterial", "$depthVSMMaterial",
                                       "$depthShadowTightMaterial", "$colpassMaterial", "$textureAnimation")]
    out = set()
    for r in refs:
        g = int(r, 16) if isinstance(r, str) else int(r)
        if g:
            out.add(g)
    for t in j.get("$textures", {}).values():
        g = tex_guid(t)
        if g:
            out.add(g)
    return out


def model_material_guids(path, remap=None):
    """Material guids of a v17 rmdl, rewriting any slot named in remap in place."""
    d = bytearray(open(path, "rb").read())
    n, idx = struct.unpack_from("<HH", d, 0x90)
    base = (idx & 0xFFFE) << (4 * (idx & 1))
    out = []
    for i in range(n):
        g = struct.unpack_from("<Q", d, base + 8 * i)[0]
        if remap and g in remap:
            g = remap[g]
            struct.pack_into("<Q", d, base + 8 * i, g)
        out.append(g)
    if remap:
        open(path, "wb").write(d)
    return out


def named_guid(kind, rel):
    stem = rel.split("/")[-1]
    if stem.startswith("0x"):
        return int(stem, 16)
    return string_to_guid("%s.rpak" % rel if rel.startswith(kind + "/") else "%s/%s.rpak" % (kind, rel))


def stage_transplant(tdir, assets):
    """Copy S21 originals exported by RSX into the assets tree."""
    for sub in ("mdl", "material", "shader", "shaderset", "texture"):
        root = os.path.join(tdir, sub)
        for r, _, fs in os.walk(root):
            for f in fs:
                src = os.path.join(r, f)
                stage(src, os.path.join(assets, os.path.relpath(src, tdir)))


def anim_entries(anim_dir, order, model_path, assets):
    """(rig guid, [aseq + arig entries]) for a model whose rig and sequences were
    converted to anim_dir, or (None, []) when the model has no animations."""
    rig = "animrig/" + model_path[len("mdl/"):-len(".rmdl")] + ".rrig"
    if rig not in order or not os.path.isfile(os.path.join(anim_dir, rig)):
        return None, []
    out = []
    for seq in order[rig]:
        stage(os.path.join(anim_dir, seq), os.path.join(assets, seq))
        out.append({"_type": "aseq", "_path": seq, "$guid": string_to_guid(seq)})
    stage(os.path.join(anim_dir, rig), os.path.join(assets, rig))
    rg = string_to_guid(rig)
    out.append({"_type": "arig", "_path": rig, "$guid": rg, "$sequences": [string_to_guid(q) for q in order[rig]]})
    return rg, out


def main(argv):
    cfg = json.load(open(argv[0]))
    assets = cfg["assets_dir"]
    resident, where = load_lists(cfg["s21_lists"])
    files = []
    packed = set()
    refs = {}
    # Model material slots TF2 itself shipped without a material, pointed at a real one.
    remap = {string_to_guid(k): string_to_guid(v) for k, v in cfg.get("material_remap", {}).items()}
    anim_order = json.load(open(cfg["anim_order"])) if cfg.get("anim_order") else {}
    anim_files = []

    for root, _, fs in os.walk(cfg["models"]):
        for f in sorted(fs):
            if not f.endswith(".rmdl"):
                continue
            src = os.path.join(root, f)
            rel = os.path.relpath(src, cfg["models"]).replace("\\", "/")
            path = "mdl/" + rel
            gid = string_to_guid(path)
            if gid in resident or gid in where:
                # Same guid as an S21 model: the S21 original is the asset.
                refs.setdefault(gid, set()).add(path)
                continue
            for ext in (".rmdl", ".vg", ".vg_static", ".phy"):
                if os.path.isfile(src[:-5] + ext):
                    stage(src[:-5] + ext, os.path.join(assets, "mdl", rel[:-5] + ext))
            entry = {"_type": "mdl_", "_path": path, "$guid": gid}
            rig, anims = anim_entries(cfg["anim_dir"], anim_order, path, assets) if anim_order else (None, [])
            if rig:
                entry["$animrigs"] = [rig]
                anim_files += anims
                packed.update(a["$guid"] for a in anims)
            files.append(entry)
            packed.add(gid)
            staged = os.path.join(assets, "mdl", rel)
            shutil.copy2(src, staged)
            for m in model_material_guids(staged, remap):
                refs.setdefault(m, set()).add(path)

    tdir = cfg.get("transplant_dir")
    if tdir:
        stage_transplant(tdir, assets)
        for r, _, fs in os.walk(os.path.join(tdir, "mdl")):
            for f in sorted(fs):
                if not f.endswith(".rmdl"):
                    continue
                rel = os.path.relpath(os.path.join(r, f), tdir).replace("\\", "/")
                gid = string_to_guid(rel)
                files.append({"_type": "mdl_", "_path": rel, "$guid": gid})
                packed.add(gid)
                for m in model_material_guids(os.path.join(r, f)):
                    refs.setdefault(m, set()).add(rel)

    # S21 originals keep their own bytes: shaders and shader sets are only ever transplanted.
    shader_entries = []
    for kind, typ in (("shader", "shdr"), ("shaderset", "shds")):
        root = os.path.join(assets, kind)
        for r, _, fs in os.walk(root):
            for f in sorted(fs):
                if not f.endswith(".msw"):
                    continue
                rel = os.path.relpath(os.path.join(r, f), assets).replace("\\", "/")[:-4]
                gid = named_guid(kind, rel)
                shader_entries.append({"_type": typ, "_path": rel, "$guid": gid})
                packed.add(gid)
                if typ == "shds":
                    d = open(os.path.join(r, f), "rb").read()
                    for ref in struct.unpack_from("<QQ", d, 5):
                        if ref:
                            refs.setdefault(ref, set()).add(rel)
    for child, parent in cfg.get("child_shaders", {}).items():
        c, pg = int(child, 16), int(parent, 16)
        shader_entries.append({"_type": "shdr", "_path": "shader/0x%016X" % c, "$guid": c, "$parentShader": pg})
        packed.add(c)
        refs.setdefault(pg, set()).add("shader/0x%016X" % c)

    # Particle materials converted from TF2 VMTs (tf2fx/particle_mats.py) ship with the map.
    pm = cfg.get("particle_mats_dir")
    if pm:
        for root, _, fs in os.walk(os.path.join(pm, "material")):
            for f in fs:
                if f.endswith((".json", ".uber")):
                    src = os.path.join(root, f)
                    dst = os.path.join(assets, os.path.relpath(src, pm))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

    mats = []
    for root, _, fs in os.walk(os.path.join(assets, "material")):
        for f in fs:
            if f.endswith(".json"):
                p = os.path.join(root, f)
                j = json.load(open(p, encoding="utf-8"))
                # Repak auto-adds a texture named by path; every texture is either listed
                # below or lives outside this pak, so the material only names guids.
                tex = {k: "0x%016X" % tex_guid(v) for k, v in j.get("$textures", {}).items()}
                if tex != j.get("$textures", {}):
                    j["$textures"] = tex
                    json.dump(j, open(p, "w", encoding="utf-8"), indent=1)
                rel = os.path.relpath(p, assets).replace("\\", "/")[:-5]
                gid = string_to_guid(rel + ".rpak")
                mats.append((rel, gid, j))
                packed.add(gid)
                for r in material_refs(j):
                    refs.setdefault(r, set()).add(rel)

    slot_map = build_slot_map([os.path.join(assets, "material")])
    tex_entries = []
    tex_roots = [(cfg["textures"], cfg["textures"])]
    if tdir:
        tex_roots.append((os.path.join(tdir, "texture"), tdir))
    dds = [(os.path.join(r, f), base) for troot, base in tex_roots
           for r, _, fs in os.walk(troot) for f in fs if f.endswith(".dds")]
    for src, base in dds:
        rel = os.path.relpath(src, base).replace("\\", "/")[:-4]
        gid = named_guid("texture", rel)
        if gid in packed:
            continue
        # texture/maps/<map>/ is per-map data: a rename leaves the old map's set in tex_src.
        parts = rel.split("/")
        if parts[:2] == ["texture", "maps"] and len(parts) > 3 and parts[2] != cfg["name"]:
            continue
        stage(src, os.path.join(assets, rel + ".dds"))
        meta = json.load(open(src[:-4] + ".json"))
        layout = meta.get("streamLayout", [])
        sm = layout.count("mandatory")
        om = layout.count("optional")
        fmt, w, h, arr, mips = read_dds_meta(src)
        perm = mips - sm - om
        gh = ("%x" % gid).lstrip("0") or "0"
        layers = meta.get("layerCount", 0)
        tail = synth_v10_tail(fmt=fmt, w=w, h=h, arr=arr, perm=perm, mand=sm, opt=om, slot=slot_map.get(gh),
                              misc_flags=layers)
        e = {"_type": "txtr", "_path": rel, "$guid": gid, "$hdrTail": tail.hex()}
        if layers:
            e["$layerCount"] = layers
        if sm or om:
            e["$strmMips"] = sm
            e["$optMips"] = om
        tex_entries.append(e)
        packed.add(gid)

    # Closure: every ref must be packed, resident, or transplanted from an S21 map pak.
    transplant, dangling = {}, {}
    for g, users in refs.items():
        if g in packed or g in resident:
            continue
        if g in where:
            transplant[g] = where[g]
        else:
            dangling[g] = sorted(users)[:3]

    files += anim_files
    # A child shader resolves its parent's bytecode when it installs, so parents go first.
    files += sorted(shader_entries, key=lambda e: (e["_type"] != "shdr", "$parentShader" in e, e["_path"]))
    files += [e for e in sorted(tex_entries, key=lambda e: e["_path"])]
    files += [{"_type": "matl", "_path": rel + ".rpak", "$guid": gid} for rel, gid, _ in sorted(mats)]

    # Particle effects converted from TF2 .pcf (tf2fx/convert_pcf.py): the map's
    # info_particle_system names resolve to them once the map pak loads.
    fx = cfg.get("effects_dir")
    if fx:
        dst_dir = os.path.join(assets, "effect")
        if os.path.isdir(dst_dir):
            shutil.rmtree(dst_dir)
        shutil.copytree(os.path.join(fx, "effect"), dst_dir)
        for e in json.load(open(os.path.join(fx, "_manifest.json"))):
            files.append({"_type": "efct", "_path": e["_path"], "$guid": e["$guid"]})
    manifest = {"version": 8, "name": cfg["name"], "assetsDir": assets.rstrip("/") + "/",
                "outputDir": cfg["out_dir"].rstrip("/") + "/", "headerFlags": 32,
                "streamFileMandatory": "paks\\Win64\\%s.starpak" % cfg["name"],
                "streamFileOptional": "paks\\Win64\\%s.opt.starpak" % cfg["name"],
                "files": files}
    json.dump(manifest, open(cfg["manifest"], "w"), indent=1)
    json.dump({"%016X" % g: v for g, v in transplant.items()}, open(cfg["manifest"] + ".transplant.json", "w"), indent=1)
    json.dump({"%016X" % g: v for g, v in dangling.items()}, open(cfg["manifest"] + ".dangling.json", "w"), indent=1)
    kinds = {}
    for g, (typ, pak, name) in transplant.items():
        kinds.setdefault(typ, 0)
        kinds[typ] += 1
    print("models %d  shaders %d  textures %d  materials %d" % (sum(1 for f in files if f["_type"] == "mdl_"),
          len(shader_entries), len(tex_entries), len(mats)))
    print("refs %d: packed/resident ok, transplant %d %s, dangling %d" % (len(refs), len(transplant), kinds, len(dangling)))
    for g, users in list(dangling.items())[:15]:
        print("  dangling 0x%016X <- %s" % (g, users))


if __name__ == "__main__":
    main(sys.argv[1:])

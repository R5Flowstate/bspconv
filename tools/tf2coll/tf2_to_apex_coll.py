"""Rebuild a Titanfall 2 map's collision as Apex BVH4 lumps.

    py tf2_to_apex_coll.py <tf2.bsp> <apex_out.bsp> --matl <dir> --surfprops <surfaceproperties.rson>

<tf2.bsp> is the original v37 map (split lumps next to it are honoured).
<apex_out.bsp> is bspconv's converted header; its sidecars are rewritten in
place: 0x03 gains the collision vertices, 0x0E gets per-model BVH fields,
0x10 / 0x11 / 0x12 / 0x13 are replaced.

Primitive mapping (every TF2 collision primitive has an exact Apex form):
  CM brush  -> convex hull leaf (faces from the brush planes, per-side surfaces)
  tricoll   -> float-vertex poly leaves, winding flipped (TF2 is clockwise)
  prop      -> static-prop reference by the same sprp index
Contents come straight from CM_UNIQUE_CONTENTS: TF2 and Apex share the bit
assignments and the per-primitive mask encoding.
"""
import argparse
import json
import os
import re
import struct
import sys

import numpy as np

from tf2_cm import TF2Collision, PRIM_BRUSH, PRIM_TRICOLL, PRIM_PROP, hull_from_planes
from apex_bvh import Writer

DMODEL_SIZE = 64


def load_surfprop_ids(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    ids = {}
    for m in re.finditer(r"^([A-Za-z0-9_]+)\s*:\s*\{(.*?)^\}", text, re.S | re.M):
        idm = re.search(r"^\s*id\s*:\s*(\d+)", m.group(2), re.M)
        if idm:
            ids[m.group(1).lower()] = int(idm.group(1))
    return ids


def load_material_surfaceprops(matl_dir):
    """material name (lowercase, forward slashes, no suffix) -> surfaceProp."""
    out = {}
    for root, _, files in os.walk(matl_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                j = json.load(open(os.path.join(root, fn), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = j.get("name", "").lower().replace("\\", "/")
            sp = j.get("surfaceProp", "")
            if name and sp:
                out.setdefault(name, sp)
    return out


def load_vmt_surfaceprops(materials_dir):
    """VPK-side .vmt materials (tools and a few world overrides) -> surfaceprop."""
    out = {}
    if not materials_dir:
        return out
    for root, _, files in os.walk(materials_dir):
        for fn in files:
            if not fn.lower().endswith(".vmt"):
                continue
            text = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
            m = re.search(r'"\$surfaceprop"\s+"([^"]+)"', text, re.I)
            if m:
                rel = os.path.relpath(os.path.join(root, fn), materials_dir)[:-4]
                out[rel.lower().replace("\\", "/")] = m.group(1)
    return out


# TF2 surface types the S21 table does not define, mapped to the closest one.
SURFPROP_ALIASES = {"cloth": "carpet"}


def yawbox_aabb(rec):
    o = rec["origin"].astype(np.float64)
    e = rec["extents"].astype(np.float64)
    c = int(np.frombuffer(bytes([rec["b0"], rec["b1"]]), "<i2")[0]) / 32768.0
    s = int(np.frombuffer(bytes([rec["b2"], rec["b3"]]), "<i2")[0]) / 32768.0
    hx = abs(c) * e[0] + abs(s) * e[1]
    hy = abs(s) * e[0] + abs(c) * e[1]
    h = np.array([hx, hy, e[2]])
    return o - h, o + h


class Converter:
    def __init__(self, tf2, surf_ids, mat_sp):
        self.tf2 = tf2
        self.surf_ids = surf_ids
        self.mat_sp = mat_sp
        self.writer = Writer(contents_masks=[int(x) for x in tf2.unique_contents])
        self.unknown_materials = set()
        self.unknown_surfprops = set()
        self.aliased_surfprops = set()
        self.name_ofs = self._name_offsets()
        self.stats = dict(hulls=0, hull_faces=0, tris=0, tris_degenerate=0, props=0)

    def _name_offsets(self):
        st = self.tf2.bsp.arr(0x2C, "<i4")
        return [int(st[int(td["name"])]) for td in self.tf2.texdata]

    def surf_type(self, texdata_idx):
        name = self.tf2.texdata_name(texdata_idx).lower().replace("\\", "/")
        sp = self.mat_sp.get(name)
        if sp is None:
            self.unknown_materials.add(name)
            return 0
        sid = self.surf_ids.get(sp.lower())
        if sid is None and sp.lower() in SURFPROP_ALIASES:
            self.aliased_surfprops.add(sp)
            sid = self.surf_ids.get(SURFPROP_ALIASES[sp.lower()])
        if sid is None:
            self.unknown_surfprops.add(sp)
            return 0
        return sid

    def surfprop(self, texdata_idx, contents_idx):
        td = self.tf2.texdata[texdata_idx]
        return self.writer.surfprop_index(int(td["flags"]) & 0xFFFF, self.surf_type(texdata_idx),
                                          contents_idx, self.name_ofs[texdata_idx])

    def brush_item(self, prim):
        hull = self.tf2.brush_hull(prim.index)
        verts, faces = hull_from_planes(hull.planes)
        if len(verts) < 4:
            raise ValueError("brush %d is degenerate" % prim.index)
        normals = np.array([p[0] for p in hull.planes], dtype=np.float64)
        surfprops = [self.surfprop(t, prim.contents_idx) for t in hull.side_texdata]
        self.stats["hulls"] += 1
        self.stats["hull_faces"] += len(faces)
        return Writer.hull_item(prim.contents_idx, verts, normals, surfprops)

    def tricoll_item(self, prim):
        idx, _ = self.tf2.tricoll_triangles(prim.index)
        p = self.tf2.verts[idx].astype(np.float64)
        p = p[:, [0, 2, 1]]
        area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
        keep = area > 1e-6
        self.stats["tris_degenerate"] += int((~keep).sum())
        p = p[keep]
        if len(p) == 0:
            return None
        self.stats["tris"] += len(p)
        texdata = int(self.tf2.tri_hdrs[prim.index]["texdata"])
        return Writer.tri_item(prim.contents_idx, self.surfprop(texdata, prim.contents_idx), p)

    def prop_item(self, prim, rec):
        mins, maxs = yawbox_aabb(rec)
        self.stats["props"] += 1
        return Writer.prop_item(prim.contents_idx, prim.index, mins, maxs)

    def run(self):
        per_model = {}
        for prim, rec in self.tf2.primitives_with_bounds():
            if prim.kind == PRIM_BRUSH:
                it = self.brush_item(prim)
            elif prim.kind == PRIM_TRICOLL:
                it = self.tricoll_item(prim)
            elif prim.kind == PRIM_PROP:
                it = self.prop_item(prim, rec)
            else:
                raise ValueError("unknown primitive type %d" % prim.kind)
            if it is not None:
                per_model.setdefault(prim.model, []).append(it)
        fields = []
        for m in range(len(self.tf2.models)):
            items = per_model.get(m, [])
            mdl = self.tf2.models[m]
            mins = mdl["mins"].astype(np.float64)
            maxs = mdl["maxs"].astype(np.float64)
            for it in items:
                mins = np.minimum(mins, it.mins)
                maxs = np.maximum(maxs, it.maxs)
            origin = np.rint((mins + maxs) * 0.5)
            span = float(np.max(np.maximum(maxs - origin, origin - mins)))
            step = 1.0
            while span / step > 32000:
                step *= 2.0
            while step > 1.0 / 64 and span / (step * 0.5) <= 32000:
                step *= 0.5
            fields.append(self.writer.build_model(items, origin, step))
        return fields


def patch_models(raw, fields, vert_offset):
    out = bytearray(raw)
    n = len(out) // DMODEL_SIZE
    if n != len(fields):
        raise ValueError("model count mismatch: lump %d, collision %d" % (n, len(fields)))
    for i, f in enumerate(fields):
        base = i * DMODEL_SIZE
        if f["empty"]:
            struct.pack_into("<4i", out, base + 32, -1, -1, 0, 0)
            struct.pack_into("<4f", out, base + 48, 0.0, 0.0, 0.0, 0.0)
            continue
        struct.pack_into("<4i", out, base + 32, f["bvh_node"], f["bvh_leaf"],
                         f["vert_index"] + vert_offset, f["flags"])
        struct.pack_into("<4f", out, base + 48, *f["origin"], f["decode_scale"])
    return bytes(out)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("tf2_bsp")
    ap.add_argument("apex_bsp")
    ap.add_argument("--matl", required=True)
    ap.add_argument("--surfprops", required=True)
    ap.add_argument("--vmt", help="unpacked VPK materials/ dir for .vmt-only materials")
    args = ap.parse_args(argv)

    tf2 = TF2Collision(args.tf2_bsp)
    mat_sp = load_vmt_surfaceprops(args.vmt)
    mat_sp.update(load_material_surfaceprops(args.matl))
    conv = Converter(tf2, load_surfprop_ids(args.surfprops), mat_sp)
    fields = conv.run()
    w = conv.writer

    lump = lambda i: "%s.%04x.bsp_lump" % (args.apex_bsp, i)
    verts_raw = open(lump(0x03), "rb").read()
    vert_offset = len(verts_raw) // 12
    models = patch_models(open(lump(0x0E), "rb").read(), fields, vert_offset)
    coll_verts = struct.pack("<%df" % (3 * len(w.verts)), *[c for v in w.verts for c in v])
    open(lump(0x03), "wb").write(verts_raw + coll_verts)
    open(lump(0x0E), "wb").write(models)
    open(lump(0x10), "wb").write(w.lump_contents())
    open(lump(0x11), "wb").write(w.lump_surfprops())
    open(lump(0x12), "wb").write(w.lump_nodes())
    open(lump(0x13), "wb").write(w.lump_leaf())

    sizes = {0x03: len(verts_raw) + len(coll_verts), 0x0E: None, 0x10: len(w.lump_contents()),
             0x11: len(w.lump_surfprops()), 0x12: len(w.nodes), 0x13: 4 * len(w.leaf)}
    hdr = bytearray(open(args.apex_bsp, "rb").read())
    for i, size in sizes.items():
        if size is not None:
            struct.pack_into("<I", hdr, 16 + 16 * i + 4, size)
    open(args.apex_bsp, "wb").write(hdr)

    s = conv.stats
    print("[coll] hulls %d (%d faces)  tris %d (dropped degenerate %d)  props %d"
          % (s["hulls"], s["hull_faces"], s["tris"], s["tris_degenerate"], s["props"]))
    print("[coll] nodes %d  leaf dwords %d  coll verts %d  contents %d  surfprops %d"
          % (len(w.nodes) // 64, len(w.leaf), len(w.verts), len(w.contents_masks), len(w.surfprops)))
    for i, f in enumerate(fields):
        if not f["empty"]:
            print("[coll] model %2d node %6d leaf %8d vert %8d origin %s step %.6g"
                  % (i, f["bvh_node"], f["bvh_leaf"], f["vert_index"] + vert_offset, f["origin"],
                     f["decode_scale"] * 65536))
    if conv.unknown_materials:
        print("[coll] WARNING %d texdata names have no extracted material (surface type 0): %s"
              % (len(conv.unknown_materials), sorted(conv.unknown_materials)))
    if conv.aliased_surfprops:
        print("[coll] NOTE surfaceProps mapped through aliases: %s"
              % sorted("%s->%s" % (a, SURFPROP_ALIASES[a.lower()]) for a in conv.aliased_surfprops))
    if conv.unknown_surfprops:
        print("[coll] WARNING surfaceProps missing from surfaceproperties.rson (type 0): %s"
              % sorted(conv.unknown_surfprops))


if __name__ == "__main__":
    main(sys.argv[1:])

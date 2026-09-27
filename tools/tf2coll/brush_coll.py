"""Give every brush entity of a converted Titanfall 2 map its *coll collision.

    py brush_coll.py <tf2.bsp> <converted dir> <map name> --target client|dedi
                     --matl <dir> --surfprops <csv> [--vmt <dir>]

Apex does not collide a brush entity ("model" "*N") through the models lump; it
decodes a standalone collision model carried in the entity itself as base64
keyvalues *coll0..*collK. TF2 has no such keys, so the engine looks up
collision that was never written. Each blob is built from the TF2 model's own
primitives with the same BVH writer as the world, in the header layout of the
target engine (v121 for the S21 client, v8 for the S3 dedi).
"""
import argparse
import base64
import os
import re
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apex_bvh import Writer  # noqa: E402
from tf2_cm import TF2Collision, PRIM_BRUSH, PRIM_TRICOLL, PRIM_PROP, hull_from_planes  # noqa: E402
from tf2_to_apex_coll import Converter, load_material_surfaceprops, load_surfprop_ids, load_vmt_surfaceprops  # noqa: E402

CHUNK = 0x78
PARTITIONS = ("env", "fx", "script", "snd", "spawn")
DMODEL_SIZE = 64
# TF2 authors these and every trigger as planes only; Apex resolves them through
# "model" "*N" + *coll. Contents, surface flags and name as stock blobs carry them.
TRIGGER_SURFACE = (0x00EB1280, 0x400, b"TOOLS\\TOOLSTRIGGER")
VOLUME_CLASSES = {
    "envmap_volume": (0x10E31240, 0x610, b"TOOLS\\TOOLSENVMAPVOLUME"),
    "light_environment_volume": TRIGGER_SURFACE,
    "light_probe_volume": TRIGGER_SURFACE,
    "trigger_soundscape": TRIGGER_SURFACE,
}


def model_items(conv, prims):
    items = []
    for prim, rec in prims:
        if prim.kind == PRIM_BRUSH:
            it = conv.brush_item(prim)
        elif prim.kind == PRIM_TRICOLL:
            it = conv.tricoll_item(prim)
        elif prim.kind == PRIM_PROP:
            it = conv.prop_item(prim, rec)
        else:
            raise ValueError("unknown primitive type %d" % prim.kind)
        if it is not None:
            items.append(it)
    return items


def build_blob(tf2, surf_ids, mat_sp, prims, model, target):
    conv = Converter(tf2, surf_ids, mat_sp)
    items = model_items(conv, prims)
    mdl = tf2.models[model]
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
    w = conv.writer
    f = w.build_model(items, origin, step)

    # Surface names are local to the blob.
    strings = tf2.bsp.raw(0x2B)
    names, local = bytearray(), {}
    surfprops = bytearray()
    for flags, surf_type, contents_idx, name_ofs in w.surfprops:
        if name_ofs not in local:
            local[name_ofs] = len(names)
            end = strings.index(b"\0", name_ofs)
            names += strings[name_ofs:end].upper() + b"\0"
        surfprops += struct.pack("<HBBI", flags, surf_type, contents_idx, local[name_ofs])
    contents = struct.pack("<%dI" % len(w.contents_masks), *w.contents_masks)
    verts = struct.pack("<%df" % (3 * len(w.verts)), *[c for v in w.verts for c in v])
    leaf = struct.pack("<%dI" % len(w.leaf), *w.leaf)

    hdr_size = 16 + (40 if target == "client" else 32)
    sp = hdr_size
    cm = sp + len(surfprops)
    sn = cm + len(contents)
    vt = sn + len(names)
    vt += -vt & 3
    lf = vt + len(verts)
    nd = lf + len(leaf)
    nd += -nd & 15
    blob = bytearray(nd + len(w.nodes))
    struct.pack_into("<4i", blob, 0, cm, sp, sn, 1)
    if target == "client":
        struct.pack_into("<6i4f", blob, 16, f["flags"], nd, vt, lf, lf, 0, *f["origin"], f["decode_scale"])
    else:
        struct.pack_into("<4i4f", blob, 16, f["flags"], nd, vt, lf, *f["origin"], f["decode_scale"])
    blob[sp:sp + len(surfprops)] = surfprops
    blob[cm:cm + len(contents)] = contents
    blob[sn:sn + len(names)] = names
    blob[vt:vt + len(verts)] = verts
    blob[lf:lf + len(leaf)] = leaf
    blob[nd:] = w.nodes
    return bytes(blob), len(items)


def volume_blob(brushes, surface, target):
    """Collision model of a plane-defined volume: one hull per brush."""
    contents, flags, name = surface
    w = Writer(contents_masks=[contents])
    sp = w.surfprop_index(flags, 0, 0, 0)
    items = []
    for planes in brushes:
        verts, _ = hull_from_planes(planes)
        normals = np.array([p[0] for p in planes], dtype=np.float64)
        items.append(Writer.hull_item(0, np.asarray(verts, dtype=np.float64), normals, [sp] * len(planes)))
    mins = np.min([it.mins for it in items], axis=0)
    maxs = np.max([it.maxs for it in items], axis=0)
    origin = np.rint((mins + maxs) * 0.5)
    span = float(np.max(np.maximum(maxs - origin, origin - mins)))
    step = 1.0
    while span / step > 32000:
        step *= 2.0
    while step > 1.0 / 64 and span / (step * 0.5) <= 32000:
        step *= 0.5
    f = w.build_model(items, origin, step)
    return pack_blob(w, f, name + b"\0", target), mins, maxs


def pack_blob(w, f, names, target):
    surfprops = b"".join(struct.pack("<HBBI", fl, st, ci, 0) for fl, st, ci, _ in w.surfprops)
    contents = struct.pack("<%dI" % len(w.contents_masks), *w.contents_masks)
    verts = struct.pack("<%df" % (3 * len(w.verts)), *[c for v in w.verts for c in v])
    leaf = struct.pack("<%dI" % len(w.leaf), *w.leaf)
    sp = 16 + (40 if target == "client" else 32)
    cm = sp + len(surfprops)
    sn = cm + len(contents)
    vt = sn + len(names)
    vt += -vt & 3
    lf = vt + len(verts)
    nd = lf + len(leaf)
    nd += -nd & 15
    blob = bytearray(nd + len(w.nodes))
    struct.pack_into("<4i", blob, 0, cm, sp, sn, 1)
    if target == "client":
        struct.pack_into("<6i4f", blob, 16, f["flags"], nd, vt, lf, lf, 0, *f["origin"], f["decode_scale"])
    else:
        struct.pack_into("<4i4f", blob, 16, f["flags"], nd, vt, lf, *f["origin"], f["decode_scale"])
    blob[sp:cm] = surfprops
    blob[cm:sn] = contents
    blob[sn:sn + len(names)] = names
    blob[vt:lf] = verts
    blob[lf:lf + len(leaf)] = leaf
    blob[nd:] = w.nodes
    return bytes(blob)


def volume_surface(classname):
    if classname in VOLUME_CLASSES:
        return VOLUME_CLASSES[classname]
    if classname.startswith("trigger_"):
        return TRIGGER_SURFACE
    return None


def plane_brushes(body):
    """*trigger_brush_<b>_plane_<p> keys -> one plane list per brush."""
    brushes = {}
    for b, p, v in re.findall(r'"\*trigger_brush_(\d+)_plane_(\d+)" "([^"]*)"', body):
        x, y, z, d = map(float, v.split())
        brushes.setdefault(int(b), []).append((int(p), ((x, y, z), d)))
    return [[pl for _, pl in sorted(planes)] for _, planes in sorted(brushes.items()) if len(planes) >= 4]


def assign_volume_models(texts, first_index, own_first):
    """Give each plane-defined entity a brush model built from all of its brushes.
    Entities without a model get a new index; ones already on a model this tool
    appended (index >= own_first) are rebuilt in place.
    -> (patched texts, {index: (surface, brushes)})"""
    volumes = {}
    nxt = first_index

    def repl(m):
        nonlocal nxt
        body = m.group(0)
        kv = dict(re.findall(r'"([^"]*)" "([^"]*)"', body))
        surface = volume_surface(kv.get("classname", ""))
        if surface is None:
            return body
        brushes = plane_brushes(body)
        if not brushes:
            return body
        mm = re.search(r'"model" "\*(\d+)"', body)
        if mm:
            idx = int(mm.group(1))
            if idx < own_first:
                return body
            body = re.sub(r'\n?"\*coll\d+" "[^"]*"', "", body)
        else:
            idx = nxt
            nxt += 1
            body = body[:-1].rstrip("\n") + '\n"model" "*%d"\n}' % idx
        volumes[idx] = (surface, brushes)
        return body

    out = {f: re.sub(r"\{[^{}]*\}", repl, t) for f, t in texts.items()}
    return out, volumes


def append_models(conv_dir, name, volumes):
    """Mesh-less dmodels for volume brush models; collision comes from *coll.
    Indices already in the lump are overwritten, new ones must extend it in order."""
    path = os.path.join(conv_dir, "%s.bsp.000e.bsp_lump" % name)
    raw = bytearray(open(path, "rb").read())
    for idx in sorted(volumes):
        mins, maxs = volumes[idx]
        rec = struct.pack("<6f2i4i4f", *mins, *maxs, 0, 0, -1, -1, 0, 0, 0.0, 0.0, 0.0, 0.0)
        count = len(raw) // DMODEL_SIZE
        if idx < count:
            raw[idx * DMODEL_SIZE:(idx + 1) * DMODEL_SIZE] = rec
        elif idx == count:
            raw += rec
        else:
            raise ValueError("model index %d does not follow the models lump (%d)" % (idx, count))
    open(path, "wb").write(raw)
    set_lump_size(os.path.join(conv_dir, name + ".bsp"), 0x0E, len(raw))


def coll_keys(blob):
    return ['"*coll%d" "%s"' % (i // CHUNK, base64.b64encode(blob[i:i + CHUNK]).decode())
            for i in range(0, len(blob), CHUNK)]


def patch_text(text, blobs):
    """Append *coll keys to every entity whose model is a brush model with a blob."""
    count = 0

    def repl(m):
        nonlocal count
        body = m.group(0)
        mm = re.search(r'"model" "\*(\d+)"', body)
        if not mm or '"*coll' in body:
            return body
        blob = blobs.get(int(mm.group(1)))
        if blob is None:
            return body
        count += 1
        return body[:-1] + "\n".join(coll_keys(blob)) + "\n}"
    return re.sub(r"\{[^{}]*\}", repl, text), count


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("tf2_bsp")
    ap.add_argument("conv_dir")
    ap.add_argument("name")
    ap.add_argument("--target", choices=("client", "dedi"), required=True)
    ap.add_argument("--matl", required=True)
    ap.add_argument("--surfprops", required=True)
    ap.add_argument("--vmt")
    args = ap.parse_args(argv)

    files = [os.path.join(args.conv_dir, "%s.bsp.0000.bsp_lump" % args.name)]
    files += [os.path.join(args.conv_dir, "%s_%s.ent" % (args.name, p)) for p in PARTITIONS]
    files = [f for f in files if os.path.isfile(f)]
    texts = {f: open(f, "rb").read().decode("latin-1") for f in files}
    tf2 = TF2Collision(args.tf2_bsp)
    n_models = os.path.getsize(os.path.join(args.conv_dir, "%s.bsp.000e.bsp_lump" % args.name)) // DMODEL_SIZE
    texts, volumes = assign_volume_models(texts, n_models, len(tf2.models))
    volume_blobs, volume_bounds = {}, {}
    for idx, (surface, brushes) in sorted(volumes.items()):
        blob, mins, maxs = volume_blob(brushes, surface, args.target)
        volume_blobs[idx] = blob
        volume_bounds[idx] = (tuple(map(float, mins)), tuple(map(float, maxs)))
        print("*%d: volume, %d brushes, %d bytes" % (idx, len(brushes), len(blob)))
    if volumes:
        append_models(args.conv_dir, args.name, volume_bounds)
    wanted = set()
    for t in texts.values():
        wanted |= {int(x) for x in re.findall(r'"model" "\*(\d+)"', t)}

    mat_sp = load_vmt_surfaceprops(args.vmt) if args.vmt else {}
    mat_sp.update(load_material_surfaceprops(args.matl))
    surf_ids = load_surfprop_ids(args.surfprops)
    per_model = {}
    for prim, rec in tf2.primitives_with_bounds():
        if prim.model in wanted:
            per_model.setdefault(prim.model, []).append((prim, rec))

    blobs = dict(volume_blobs)
    for m in sorted(wanted - set(volume_blobs)):
        blob, n = build_blob(tf2, surf_ids, mat_sp, per_model.get(m, []), m, args.target)
        blobs[m] = blob
        print("*%d: %d primitives, %d bytes" % (m, n, len(blob)))

    for f, t in texts.items():
        new, n = patch_text(t, blobs)
        if n or new != open(f, "rb").read().decode("latin-1"):
            data = new.encode("latin-1")
            open(f, "wb").write(data)
            if f.endswith(".bsp_lump"):
                set_lump_size(os.path.join(args.conv_dir, args.name + ".bsp"), 0x00, len(data))
            print("%s: %d entities" % (os.path.basename(f), n))
    return 0


def set_lump_size(bsp, lump, size):
    """The engine reads a split lump at the length its header records."""
    hdr = bytearray(open(bsp, "rb").read())
    struct.pack_into("<I", hdr, 16 + 16 * lump + 4, size)
    open(bsp, "wb").write(hdr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

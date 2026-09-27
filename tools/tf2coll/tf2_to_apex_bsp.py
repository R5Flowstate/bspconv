"""Convert a Titanfall 2 (rBSP v37) map to the Apex bridge targets.

    py tf2_to_apex_bsp.py <tf2.bsp> <out_dir> --target client|dedi --matl <dir> --surfprops <rson> [--vmt <dir>]

client -> rBSP v51 flags 1, the full lump set the S21 client loads.
dedi   -> rBSP v47 flags 0, the server lump set the S3 dedicated server loads.

Every lump is rewritten to the struct the target engine reads; nothing is
copied because its size happens to match. Layouts come from the engine's own
loaders.
"""
import argparse
import os
import re
import struct
import sys

import numpy as np

from bc4 import encode_bc5
from tf2_cm import TF2Collision
from tf2_to_apex_coll import (Converter, load_material_surfaceprops, load_surfprop_ids,
                              load_vmt_surfaceprops)

HEADER_SIZE = 16 + 128 * 16

# Server lumps the S3 dedicated server reads (collision, entities, vis, props, level info).
DEDI_LUMPS = {0x00, 0x01, 0x02, 0x03, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x18, 0x23,
              0x25, 0x27, 0x36, 0x50, 0x52, 0x55, 0x6A, 0x6B, 0x77, 0x78, 0x79, 0x7B}

SPRP_ID = 0x73707270


def ent_model_paths(text):
    """models/foo.mdl -> mdl/foo.rmdl, the path form Apex model assets use."""
    return re.sub(r'"models([/\\][^"]*?)\.mdl"',
                  lambda m: '"mdl%s.rmdl"' % m.group(1).replace("\\", "/"), text)


class Tf2Map:
    def __init__(self, path):
        self.cm = TF2Collision(path)
        self.bsp = self.cm.bsp
        self.path = path

    def raw(self, i):
        return self.bsp.raw(i)


# --- per-lump transforms ---------------------------------------------------

def conv_texdata(m):
    td = m.cm.texdata
    st = m.bsp.arr(0x2C, "<i4")
    out = np.zeros(len(td), dtype=[("name", "<i4"), ("w", "<i4"), ("h", "<i4"), ("flags", "<i4")])
    out["name"] = st[td["name"]]
    out["w"] = td["w"]
    out["h"] = td["h"]
    out["flags"] = td["flags"]
    return out.tobytes()


def conv_vertices_unlit(raw):
    # TF2 and Apex v47+ share it: pos, normal, uv, RGBA.
    if len(raw) % 20:
        raise ValueError("unlit vertex lump size")
    return raw


def conv_vertices_lit_flat(raw):
    # TF2 36B (pos, normal, uv, RGBA, lmapUV, lmapStep) -> 28B (drop lmapStep).
    a = np.frombuffer(raw, np.uint8).reshape(-1, 36)
    return np.ascontiguousarray(a[:, :28]).tobytes()


def conv_vertices_lit_bump(raw):
    # TF2 44B (pos, normal, uv, RGBA, lmapUV, lmapStep, tangent, 0) -> 32B (pos..lmapUV, tangent).
    a = np.frombuffer(raw, np.uint8).reshape(-1, 44)
    return np.ascontiguousarray(np.concatenate([a[:, :28], a[:, 36:40]], axis=1)).tobytes()


def conv_vertices_unlit_ts(raw):
    # TF2 28B (pos, normal, uv, RGBA, tangent, 0) -> 24B.
    a = np.frombuffer(raw, np.uint8).reshape(-1, 28)
    return np.ascontiguousarray(a[:, :24]).tobytes()


def conv_rtl(raw, headers):
    """Real-time light texels, per lightmap page.
    TF2: two w*h RGBA8 planes -- light ids (four 6-bit slots, 63 = none, light
    group in the top byte) and the visibility of each slot.
    Apex: the same id plane as R32_UINT, then the visibilities as BC5 over
    w x 2h (slots 0/1 in the top half, 2/3 in the bottom half). The header
    also counts a w*h/2 BC4 plane that is not stored.
    -> (lump bytes, size the header records)"""
    src = np.frombuffer(raw, np.uint8)
    out, disk, o = bytearray(), 0, 0
    for i in range(0, len(headers), 8):
        _, _, _, w, h = struct.unpack_from("<BBHHH", headers, i)
        n = w * h
        ids = src[o:o + 4 * n].reshape(-1, 4).copy()
        vis = src[o + 4 * n:o + 8 * n].reshape(h, w, 4)
        o += 8 * n
        ids[ids[:, 3] == 255, 3] = 0
        r = np.concatenate([vis[..., 0], vis[..., 2]])
        g = np.concatenate([vis[..., 1], vis[..., 3]])
        out += ids.tobytes() + encode_bc5(r, g).tobytes()
        disk += 4 * n + ((w + 3) >> 2) * 16 * ((2 * h + 3) >> 2) + (n >> 1)
    if o != len(raw):
        raise ValueError("real-time light lump does not match the lightmap headers")
    return bytes(out), disk


WL_TYPE_SKYLIGHT = 3
WL_FLAG_REALTIME = 0x8


def conv_lightmaps(raw, headers, rtl, rtl_groups, worldlights):
    """Sun visibility into lightmapTexture1.a, per lightmap page.
    TF2 leaves a coverage mask (0/255) in that alpha and reads the sun's static
    shadow from the real-time light texels: the first slot whose light is a
    sky light gives the visibility. The Apex world shader reads the alpha as
    the sun visibility itself, so a coverage mask lights every texel."""
    wl = np.frombuffer(worldlights, np.uint8).reshape(-1, 112)
    wl_type = wl[:, 52:56].copy().view("<i4").ravel()
    wl_flags = wl[:, 88:92].copy().view("<i4").ravel()
    realtime = np.flatnonzero(wl_flags & WL_FLAG_REALTIME)
    is_sky = wl_type[realtime] == WL_TYPE_SKYLIGHT
    table = np.frombuffer(rtl_groups, "<u2").astype(np.int64)

    out = bytearray(raw)
    lm = np.frombuffer(out, np.uint8)
    src = np.frombuffer(rtl, np.uint8)
    o = 0
    for i in range(0, len(headers), 8):
        _, _, _, w, h = struct.unpack_from("<BBHHH", headers, i)
        n = w * h
        ids = src[o:o + 4 * n].copy().view("<u4")
        vis = src[o + 4 * n:o + 8 * n].reshape(n, 4)
        plane1 = lm[o + 4 * n:o + 8 * n].reshape(n, 4)
        group = (ids >> 24).astype(np.int64)
        sun = np.zeros(n, np.uint8)
        found = np.zeros(n, bool)
        for s in range(4):
            slot = ((ids >> (6 * s)) & 63).astype(np.int64)
            idx = group * 63 + slot
            # An out-of-range group-table read returns 0 in the TF2 shader.
            rt = np.where(idx < len(table), table[np.minimum(idx, len(table) - 1)], 0)
            hit = ~found & (slot != 63) & (rt < len(realtime)) & is_sky[np.minimum(rt, len(realtime) - 1)]
            sun[hit] = vis[hit, s]
            found |= hit
        plane1[:, 3] = sun
        o += 8 * n
    if o != len(raw):
        raise ValueError("lightmap lump does not match the lightmap headers")
    return bytes(out)


def conv_meshes(raw):
    # dmesh_t is field-for-field TF2's, except +20: TF2 luxel extents, Apex fade distance (0 = none).
    a = np.frombuffer(raw, np.uint8).reshape(-1, 28).copy()
    a[:, 20:22] = 0
    return a.tobytes()


def conv_lightprobes_v51(raw):
    """48B probe -> 44B, exactly what the S21 loader does for maps older than v51:
    SH and sky ints copied, light index +32 where its weight is set, empty
    slots compacted to the front, trailing 4 bytes dropped."""
    n = len(raw) // 48
    if n * 48 != len(raw):
        raise ValueError("lightprobe lump size")
    src = np.frombuffer(raw, np.uint8).reshape(n, 48)
    out = np.zeros((n, 44), np.uint8)
    out[:, :32] = src[:, :32]
    w = src[:, 32:36].copy()
    idx = src[:, 36:44].copy().view("<u2").reshape(n, 4).astype(np.int64)
    idx = np.where(w != 0, idx + 32, idx) & 0xFFFF
    for i in range(3):
        for j in range(i + 1, 4):
            move = (w[:, i] == 0) & (w[:, j] != 0)
            w[move, i] = w[move, j]
            idx[move, i] = idx[move, j]
            w[move, j] = 0
            idx[move, j] = 0xFFFF
    out[:, 32:36] = w
    out[:, 36:44] = idx.astype("<u2").view(np.uint8).reshape(n, 8)
    return out.tobytes()


def conv_aabb_nodes(raw):
    """TF2 {mins, u8 childCount, u8 objRefCount, u16 totalRefs, maxs, u16 firstChild, u16 objRef}
    -> Apex mcell_aabb_t {mins, children = count|first<<8, maxs, objRefs = count|offset<<8}
    plus the per-node subtree totals the Apex engine keeps in a separate lump."""
    a = np.frombuffer(raw, np.uint32).reshape(-1, 8).copy()
    w12 = a[:, 3].copy()
    w28 = a[:, 7].copy()
    child_count = w12 & 0xFF
    ref_count = (w12 >> 8) & 0xFF
    total = (w12 >> 16) & 0xFFFF
    first_child = w28 & 0xFFFF
    ref_ofs = (w28 >> 16) & 0xFFFF
    a[:, 3] = child_count | (first_child << 8)
    a[:, 7] = ref_count | (ref_ofs << 8)
    # Cross-check the stored totals against the tree.
    n = len(a)
    sub = np.zeros(n, np.int64)
    for i in range(n - 1, -1, -1):
        s = int(ref_count[i])
        for c in range(int(first_child[i]), int(first_child[i] + child_count[i])):
            if c <= i:
                raise ValueError("aabb child %d not after parent %d" % (c, i))
            s += int(sub[c])
        sub[i] = s
    if not np.array_equal(sub, total.astype(np.int64)):
        raise ValueError("aabb subtree totals disagree with the stored counts")
    return a.tobytes(), total.astype("<u4").tobytes()


def widen_u16(raw):
    return np.frombuffer(raw, "<u2").astype("<u4").tobytes()


def csm_light_basis(shadow_dir):
    """Rows x', y', z' of the frame the CSM AABBs are stored in (z' = -shadow direction)."""
    z = -np.asarray(shadow_dir, np.float64)
    z /= np.linalg.norm(z)
    x = np.cross((0.0, 0.0, 1.0), z)
    if np.linalg.norm(x) < 1e-6:
        raise ValueError("vertical shadow direction: CSM light frame undefined")
    x /= np.linalg.norm(x)
    return np.array([x, np.cross(z, x), z])


def conv_csm(nodes_raw, refs_raw, envs_raw, sm_raw, opaque_raw, alpha_raw, idx_raw):
    """CSM caster tree 0x63/0x64 (+ totals 0x26) and shadow environments 0x05.

    The Apex static-ortho cull sets a world shadow mesh's vis bit only when a CSM
    node references it; the culled shadow-mesh pass and the depth-only world pass
    draw nothing else. TF2 references only static props there, so every world
    shadow mesh is added once, at the deepest node of its environment whose
    light-space box contains the mesh (the environment root grows if none does).
    Refs are re-laid out in preorder: a node's own refs, then its subtree."""
    a = np.frombuffer(nodes_raw, np.uint32).reshape(-1, 8).copy()
    box = a.view(np.float32)
    w12, w28 = a[:, 3].copy(), a[:, 7].copy()
    child_count = (w12 & 0xFF).astype(np.int64)
    ref_count = ((w12 >> 8) & 0xFF).astype(np.int64)
    first_child = (w28 & 0xFFFF).astype(np.int64)
    ref_ofs = ((w28 >> 16) & 0xFFFF).astype(np.int64)
    refs = np.frombuffer(refs_raw, "<u2").astype(np.int64)
    own = [list(refs[ref_ofs[i]:ref_ofs[i] + ref_count[i]]) for i in range(len(a))]

    sm = np.frombuffer(sm_raw, np.dtype([("vtx", "<u4"), ("tris", "<u4"), ("draw", "<u2"), ("mtl", "<u2")]))
    opaque = np.frombuffer(opaque_raw, "<f4").reshape(-1, 3)
    alpha = np.frombuffer(alpha_raw, np.uint8).reshape(-1, 20)[:, :12].copy().view("<f4").reshape(-1, 3)
    idx = np.frombuffer(idx_raw, "<u2").astype(np.int64)
    idx_start = np.concatenate([[0], np.cumsum(sm["tris"].astype(np.int64) * 3)])
    if idx_start[-1] != len(idx):
        raise ValueError("shadow mesh triangle counts do not cover the index lump")

    envs = bytearray(envs_raw)
    env_fields = [list(struct.unpack_from("<6I3f", envs, 36 * e)) for e in range(len(envs) // 36)]
    added = grown = 0
    for f in env_fields:
        root, aabb_end, sm_begin, sm_end = f[0], f[3], f[2], f[5]
        basis = csm_light_basis(f[6:9])
        for k in range(sm_begin, sm_end):
            verts = opaque if sm["draw"][k] == 1 else alpha
            pts = verts[idx[idx_start[k]:idx_start[k + 1]] + int(sm["vtx"][k])] @ basis.T
            lo, hi = pts.min(0), pts.max(0)
            node = root
            if not (np.all(lo >= box[node, 0:3]) and np.all(hi <= box[node, 4:7])):
                box[node, 0:3] = np.minimum(box[node, 0:3], lo)
                box[node, 4:7] = np.maximum(box[node, 4:7], hi)
                grown += 1
            while True:
                for c in range(first_child[node], first_child[node] + child_count[node]):
                    if c < aabb_end and np.all(lo >= box[c, 0:3]) and np.all(hi <= box[c, 4:7]):
                        node = c
                        break
                else:
                    break
            own[node].append(k)
            added += 1

    out_refs, total = [], np.zeros(len(a), np.int64)
    new_ofs = np.zeros(len(a), np.int64)
    visited = np.zeros(len(a), bool)

    def lay(i):
        visited[i] = True
        new_ofs[i] = len(out_refs)
        out_refs.extend(own[i])
        for c in range(first_child[i], first_child[i] + child_count[i]):
            lay(c)
        total[i] = len(out_refs) - new_ofs[i]

    for f in env_fields:
        f[1] = len(out_refs)
        lay(f[0])
        f[4] = len(out_refs)
        if not visited[f[0]:f[3]].all():
            raise ValueError("CSM environment nodes %d..%d are not one tree" % (f[0], f[3]))
    if not visited.all():
        raise ValueError("CSM nodes outside every shadow environment")
    counts = np.array([len(o) for o in own], np.int64)
    if counts.max() > 0xFF or len(out_refs) > 0x7FFFFF:
        raise ValueError("CSM ref count exceeds the node field")
    for e, f in enumerate(env_fields):
        struct.pack_into("<6I3f", envs, 36 * e, *f)

    a[:, 3] = child_count | (first_child << 8)
    a[:, 7] = counts | (new_ofs << 8)
    return (a.tobytes(), np.asarray(out_refs, "<u4").tobytes(), total.astype("<u4").tobytes(),
            bytes(envs), added, grown)


def conv_level_info(raw, num_props):
    fd, ft, fs, nprops, sx, sy, sz = struct.unpack("<4I3f", raw)
    if nprops != num_props:
        raise ValueError("level info prop count %d != sprp %d" % (nprops, num_props))
    first_prop = (fs + 63) & ~63
    return struct.pack("<5I3fI", fd, ft, fs, first_prop, nprops, sx, sy, sz, 0)


def conv_cubemaps(raw):
    n = len(raw) // 16
    a = np.frombuffer(raw, "<i4").reshape(n, 4).copy()
    # TF2 stores the capture size; Apex stores the cubemap texture guid, filled by the asset step.
    a[:, 3] = 0
    return a.tobytes(), np.full(n, 1.0, "<f4").tobytes()


def conv_game_lump(raw, version):
    count = struct.unpack_from("<i", raw, 0)[0]
    entries = [struct.unpack_from("<iHHii", raw, 4 + 16 * i) for i in range(count)]
    hdr_ofs = struct.unpack_from("<i", raw, 4 + 8)[0] - (4 + 16 * count)
    for gid, _, ver, fofs, flen in entries:
        if gid != SPRP_ID:
            continue
        d = raw[fofs - hdr_ofs:fofs - hdr_ofs + flen]
        nd = struct.unpack_from("<i", d, 0)[0]
        names = []
        for i in range(nd):
            n = d[4 + 128 * i:4 + 128 * (i + 1)].split(b"\0")[0].decode()
            n = n.replace("\\", "/")
            if n.startswith("models/") and n.endswith(".mdl"):
                n = "mdl/" + n[len("models/"):-len(".mdl")] + ".rmdl"
            names.append(n)
        o = 4 + 128 * nd
        counts = struct.unpack_from("<3I", d, o)
        o += 12
        props = np.frombuffer(d, np.uint8, counts[0] * 64, o).reshape(-1, 64).copy()
        o += counts[0] * 64
        # Trailing count of an optional per-prop section; every TF2 and Apex map seen has 0.
        tail_count = struct.unpack_from("<I", d, o)[0]
        if tail_count != 0 or o + 4 != len(d):
            raise ValueError("sprp trailing section not empty (%d, %d bytes)" % (tail_count, len(d) - o))
        # +52 diffuse modulation stays white, +56 wind data none, +60 set-dress level 0.
        props[:, 52:56] = 0xFF
        props[:, 56:60] = 0
        props[:, 60] = 0
        body = struct.pack("<i", nd)
        for n in names:
            b = n.encode()
            if len(b) >= 128:
                raise ValueError("model path too long: %s" % n)
            body += b + b"\0" * (128 - len(b))
        body += struct.pack("<3I", *counts) + props.tobytes() + struct.pack("<I", 0)
        out = struct.pack("<i", 1) + struct.pack("<iHHii", SPRP_ID, 0, version, 20, len(body)) + body
        return out, names, counts[0]
    raise ValueError("no sprp game lump")


# --- driver ------------------------------------------------------------------

def build(tf2_path, target, matl, surfprops, vmt):
    m = Tf2Map(tf2_path)
    version = 51 if target == "client" else 47
    L = {}
    # Lumps whose header length is not the length of the data written (see conv_rtl).
    sizes = {}

    mat_sp = load_vmt_surfaceprops(vmt)
    mat_sp.update(load_material_surfaceprops(matl))
    coll = Converter(m.cm, load_surfprop_ids(surfprops), mat_sp)
    fields = coll.run()
    w = coll.writer

    verts = m.raw(0x03)
    vert_offset = len(verts) // 12
    L[0x03] = verts + struct.pack("<%df" % (3 * len(w.verts)), *[c for v in w.verts for c in v])

    models = np.zeros((len(m.cm.models), 64), np.uint8)
    src = np.frombuffer(m.raw(0x0E), np.uint8).reshape(-1, 32)
    models[:, :32] = src
    for i, f in enumerate(fields):
        struct.pack_into("<4i4f", models[i], 32, f["bvh_node"], f["bvh_leaf"],
                         f["vert_index"] + vert_offset, f["flags"], *f["origin"], f["decode_scale"])
    L[0x0E] = models.tobytes()
    L[0x10] = w.lump_contents()
    L[0x11] = w.lump_surfprops()
    L[0x12] = w.lump_nodes()
    L[0x13] = w.lump_leaf()

    L[0x00] = ent_model_paths(m.raw(0x00).decode("latin-1")).encode("latin-1")
    L[0x01] = m.raw(0x01)
    L[0x02] = conv_texdata(m)
    L[0x0F] = m.raw(0x2B)
    L[0x18] = m.raw(0x18)
    L[0x23], prop_names, num_props = conv_game_lump(m.raw(0x23), version)
    L[0x36] = m.raw(0x36)
    L[0x7B] = conv_level_info(m.raw(0x7B), num_props)

    L[0x77], L[0x25] = conv_aabb_nodes(m.raw(0x77))
    L[0x78] = widen_u16(m.raw(0x78))
    L[0x79] = m.raw(0x79)
    for i in range(0x6A, 0x77):
        L[i] = m.raw(i)

    if target == "client":
        L[0x04] = m.raw(0x04)
        L[0x1E] = m.raw(0x1E)
        L[0x2A], L[0x2B] = conv_cubemaps(m.raw(0x2A))
        L[0x47] = conv_vertices_unlit(m.raw(0x47))
        if m.raw(0x48):
            L[0x48] = conv_vertices_lit_flat(m.raw(0x48))
        L[0x49] = conv_vertices_lit_bump(m.raw(0x49))
        L[0x4A] = conv_vertices_unlit_ts(m.raw(0x4A))
        L[0x4F] = m.raw(0x4F)
        L[0x50] = conv_meshes(m.raw(0x50))
        L[0x51] = m.raw(0x51)
        L[0x52] = m.raw(0x52)
        L[0x53] = m.raw(0x53)
        L[0x62] = conv_lightmaps(m.raw(0x62), m.raw(0x53), m.raw(0x69), m.raw(0x7A), m.raw(0x36))
        L[0x69], sizes[0x69] = conv_rtl(m.raw(0x69), m.raw(0x53))
        L[0x7A] = m.raw(0x7A)
        L[0x63], L[0x64], L[0x26], L[0x05], added, grown = conv_csm(
            m.raw(0x63), m.raw(0x64), m.raw(0x05), m.raw(0x7F), m.raw(0x7C), m.raw(0x7D), m.raw(0x7E))
        print("[bsp] csm: %d world shadow meshes referenced, %d grew their environment root" % (added, grown))
        L[0x65] = conv_lightprobes_v51(m.raw(0x65))
        L[0x66] = m.raw(0x66)
        L[0x67] = m.raw(0x67)
        L[0x68] = m.raw(0x68)
        for i in range(0x7C, 0x80):
            L[i] = m.raw(i)
    else:
        L[0x50] = conv_meshes(m.raw(0x50))
        L[0x52] = m.raw(0x52)
        L = {k: v for k, v in L.items() if k in DEDI_LUMPS}

    return m, L, sizes, version, coll, prop_names


def apex_ent_header(text):
    """TF2 partitions start 'ENTITIES01'; the Apex loader requires
    'ENTITIES%d num_models=%d' and only checks that both fields parse."""
    first, _, rest = text.partition("\n")
    if first.startswith("ENTITIES") and "num_models=" not in first:
        return "ENTITIES02 num_models=1\n" + rest
    return text


def write(out_dir, name, m, L, sizes, version, flags):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name + ".bsp")
    for f in os.listdir(out_dir):
        if f.startswith(name + ".bsp."):
            os.remove(os.path.join(out_dir, f))
    hdr = bytearray(HEADER_SIZE)
    struct.pack_into("<4sHHII", hdr, 0, b"rBSP", version, flags, m.bsp.revision, 127)
    for i, data in sorted(L.items()):
        if not data:
            continue
        struct.pack_into("<4I", hdr, 16 + 16 * i, 0, sizes.get(i, len(data)), 0, 0)
        with open("%s.%04x.bsp_lump" % (base, i), "wb") as f:
            f.write(data)
    with open(base, "wb") as f:
        f.write(hdr)
    src_dir = os.path.dirname(m.path)
    src_name = os.path.basename(m.path)[:-4]
    for part in ("env", "fx", "script", "snd", "spawn"):
        p = os.path.join(src_dir, "%s_%s.ent" % (src_name, part))
        if os.path.isfile(p):
            text = apex_ent_header(open(p, "rb").read().decode("latin-1"))
            with open(os.path.join(out_dir, "%s_%s.ent" % (name, part)), "wb") as f:
                f.write(ent_model_paths(text).encode("latin-1"))
    return base


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("tf2_bsp")
    ap.add_argument("out_dir")
    ap.add_argument("--target", choices=("client", "dedi"), required=True)
    ap.add_argument("--name", help="output map name (default: source name)")
    ap.add_argument("--matl", required=True)
    ap.add_argument("--surfprops", required=True)
    ap.add_argument("--vmt")
    args = ap.parse_args(argv)
    m, L, sizes, version, coll, names = build(args.tf2_bsp, args.target, args.matl, args.surfprops, args.vmt)
    name = args.name or os.path.basename(args.tf2_bsp)[:-4]
    base = write(args.out_dir, name, m, L, sizes, version, 1 if args.target == "client" else 0)
    print("[bsp] %s v%d: %d lumps -> %s" % (args.target, version, sum(1 for v in L.values() if v), base))
    print("[bsp] sprp models %d, collision %s" % (len(names), coll.stats))
    if coll.unknown_materials or coll.unknown_surfprops:
        print("[bsp] WARNING surface type gaps: %s %s" % (sorted(coll.unknown_materials), sorted(coll.unknown_surfprops)))


if __name__ == "__main__":
    main(sys.argv[1:])

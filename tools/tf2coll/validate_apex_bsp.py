"""Offline load gate for a v47/v51 Apex rBSP: every size and cross-reference
rule the engine's map loaders enforce or silently rely on.

    py validate_apex_bsp.py <map.bsp>

Exits nonzero on the first class of failure it finds, after printing all of
them. The rules mirror the S21 client loaders (Map_LoadModelGuts and its
callees, Mod_LoadPortalsCells, Mod_LoadShadowPVS, CollisionBSPData_Load) and
the S3 dedicated server's subset.
"""
import os
import struct
import sys

import numpy as np


class Map:
    def __init__(self, path):
        self.path = path
        h = open(path, "rb").read(16 + 128 * 16)
        self.magic, self.version, self.flags = struct.unpack_from("<4sHH", h, 0)
        self.hdr = [struct.unpack_from("<4I", h, 16 + 16 * i) for i in range(128)]

    def raw(self, i):
        p = "%s.%04x.bsp_lump" % (self.path, i)
        return open(p, "rb").read() if os.path.isfile(p) else b""

    def size(self, i):
        return len(self.raw(i))


def main(argv):
    m = Map(argv[0])
    errs = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    need(m.magic == b"rBSP", "bad magic")
    need(m.version in (47, 51), "unexpected version %d" % m.version)
    for i in range(128):
        if m.hdr[i][1] != m.size(i):
            errs.append("lump %02x header len %d != sidecar %d" % (i, m.hdr[i][1], m.size(i)))

    client = m.version >= 51
    s = m.size

    need(s(0x01) % 16 == 0, "planes % 16")
    need(s(0x02) % 16 == 0 and 1 <= s(0x02) // 16 <= 2048, "texdata count")
    need(s(0x03) % 12 == 0, "vertices % 12")
    need(s(0x0E) % 64 == 0 and s(0x0E) > 0, "models % 64")
    need(s(0x10) % 4 == 0, "contents masks % 4")
    need(s(0x11) % 8 == 0, "surface properties % 8")
    need(s(0x12) % 64 == 0, "bvh nodes % 64")
    need(s(0x13) % 4 == 0, "bvh leaf % 4")
    need(s(0x7B) == 36, "level info must be 36 bytes for v>=46")

    names = m.raw(0x0F)
    td = np.frombuffer(m.raw(0x02), "<i4").reshape(-1, 4)
    for i, row in enumerate(td):
        need(0 <= row[0] < len(names), "texdata %d name offset out of range" % i)
    sp = np.frombuffer(m.raw(0x11), np.dtype([("f", "<u2"), ("t", "u1"), ("c", "u1"), ("n", "<u4")]))
    ncm = s(0x10) // 4
    need((sp["c"] < ncm).all(), "surfprop contents index out of range")
    need((sp["n"] < len(names)).all(), "surfprop name offset out of range")

    models = np.frombuffer(m.raw(0x0E), "<i4").reshape(-1, 16)
    nverts = s(0x03) // 12
    nnodes = s(0x12) // 64
    nleaf = s(0x13) // 4
    for i, r in enumerate(models):
        if r[8] < 0:
            continue
        need(r[8] < nnodes, "model %d bvh node out of range" % i)
        need(r[9] <= nleaf, "model %d bvh leaf out of range" % i)
        need(r[10] <= nverts, "model %d vert base out of range" % i)

    li = struct.unpack("<5I3fI", m.raw(0x7B))
    need(li[3] == (li[2] + 63) & ~63, "level info firstStaticPropIdx != align64(firstSky)")

    # Static props.
    g = m.raw(0x23)
    cnt = struct.unpack_from("<i", g, 0)[0]
    num_props = None
    for k in range(cnt):
        gid, _, ver, fo, fl = struct.unpack_from("<iHHii", g, 4 + 16 * k)
        if gid == 0x73707270:
            need(fo == m.hdr[0x23][0] + 4 + 16 * cnt, "sprp fileofs must be lump ofs + directory size")
            d = g[fo - m.hdr[0x23][0]:fo - m.hdr[0x23][0] + fl]
            nd = struct.unpack_from("<i", d, 0)[0]
            o = 4 + 128 * nd
            c = struct.unpack_from("<3I", d, o)
            num_props = c[0]
            props = np.frombuffer(d, np.uint8, 64 * c[0], o + 12).reshape(-1, 64)
            ptype = props[:, 28:30].copy().view("<u2").ravel()
            need((ptype < nd).all(), "static prop type out of dictionary")
            need(len(d) == o + 12 + 64 * c[0] + 4, "sprp size")
            for i in range(nd):
                n = d[4 + 128 * i:4 + 128 * i + 128].split(b"\0")[0]
                if not (n.startswith(b"mdl/") and n.endswith(b".rmdl")):
                    errs.append("sprp dict entry not an rmdl path: %s" % n)
                    break
    need(num_props is not None, "no sprp")
    if num_props is not None:
        need(li[4] == num_props, "level info numStaticProps != sprp count")

    # Vis: cells, portals, AABB tree, obj refs.
    naabb = s(0x77) // 32
    nobj = s(0x78) // 4
    need(s(0x79) % 32 == 0 and s(0x79) // 32 == nobj, "obj ref bounds count != obj refs")
    # Stock maps store a prefix of the nodes; the engine only checks the stride.
    need(s(0x25) % 4 == 0 and s(0x25) <= 4 * naabb, "aabb num-obj-refs-total: at most one u32 per node")
    need(s(0x27) in (0, 2 * naabb), "aabb fade dists size")
    aabb = np.frombuffer(m.raw(0x77), "<u4").reshape(-1, 8)
    ch = aabb[:, 3]
    ob = aabb[:, 7]
    need((((ch & 0xFF) == 0) | (((ch >> 8) & 0xFFFF) + (ch & 0xFF) <= naabb)).all(), "aabb child range")
    need((((ob >> 8) & 0xFFFF) + (ob & 0xFF) <= nobj).all(), "aabb obj ref range")
    objs = np.frombuffer(m.raw(0x78), "<u4")
    if num_props is not None and len(objs):
        need(objs.max() < li[3] + num_props, "obj ref beyond prop id space")
    for lump, stride in ((0x6A, 8), (0x6B, 8), (0x6C, 12), (0x6D, 12), (0x6E, 4), (0x6F, 16),
                         (0x70, 2), (0x71, 2), (0x72, 16), (0x73, 16), (0x74, 8), (0x75, 12), (0x76, 2)):
        need(s(lump) % stride == 0, "lump %02x stride %d" % (lump, stride))
    need(s(0x6F) // 16 == s(0x6D) // 12, "portal vert edges != portal verts")
    need(s(0x70) == s(0x71), "portal vert refs != edge refs")
    need(s(0x72) == s(0x73), "portal isect edge != at vert")
    need(s(0x74) // 8 == s(0x6E) // 4, "portal isect headers != edges")

    if client:
        nmesh = s(0x50) // 28
        need(s(0x50) % 28 == 0, "meshes % 28")
        need(s(0x52) % 12 == 0, "material sorts % 12")
        need(s(0x51) == 32 * nmesh, "mesh bounds count != meshes")
        need(s(0x1E) % 12 == 0, "vertex normals % 12")
        need(s(0x4F) % 2 == 0, "mesh indices % 2")
        for lump, stride in ((0x47, 20), (0x48, 28), (0x49, 32), (0x4A, 24)):
            need(s(lump) % stride == 0, "vertex lump %02x stride %d" % (lump, stride))
        need(s(0x65) % 44 == 0, "lightprobes % 44 at v51")
        need(s(0x67) % 8 == 0 and s(0x68) % 20 == 0, "lightprobe tree / refs")
        need(s(0x04) % 28 == 0, "lightprobe parent infos % 28")
        need(s(0x05) % 36 == 0, "shadow environments % 36")
        need(s(0x36) % 112 == 0 and s(0x36) // 112 <= 16352, "world lights")
        need(s(0x2A) % 16 == 0 and s(0x2B) == s(0x2A) // 4, "cubemap ambient count != cubemaps")
        need(s(0x53) % 8 == 0, "lightmap headers % 8")
        need(s(0x63) % 32 == 0 and s(0x64) % 4 == 0 and s(0x26) % 4 == 0 and s(0x26) <= s(0x63) // 8, "csm tree")
        need(s(0x7C) % 12 == 0 and s(0x7D) % 20 == 0 and s(0x7E) % 2 == 0 and s(0x7F) % 12 == 0, "shadow mesh")

        # Lightmap sky data must equal the engine's per-format sum.
        lh = np.frombuffer(m.raw(0x53), np.uint8).reshape(-1, 8)
        total = 0
        for row in lh:
            t = int(row[0])
            w, h = struct.unpack_from("<2H", row.tobytes(), 4)
            if ((t - 1) & 0xF6) == 0 and t != 2:
                total += {1: 8, 10: 8, 9: 12}[t] * w * h
        need(total == s(0x62), "lightmap sky size %d != engine formula %d" % (s(0x62), total))

        meshes = np.frombuffer(m.raw(0x50), np.dtype([
            ("fi", "<u4"), ("tri", "<u2"), ("fv", "<u2"), ("lv", "<u2"), ("vt", "u1"), ("cube", "u1"),
            ("st", "u1", 4), ("lux", "<u2", 2), ("fade", "<u2"), ("ms", "<u2"), ("flags", "<u4")]))
        sorts = np.frombuffer(m.raw(0x52), np.dtype([("td", "<u2"), ("lm", "<i2"), ("u", "<u2"),
                                                     ("lv", "<u2"), ("fv", "<u4")]))
        need((meshes["ms"] < len(sorts)).all(), "mesh material sort out of range")
        need((sorts["td"] < len(td)).all(), "material sort texdata out of range")
        nlm = s(0x53) // 8
        need(((sorts["lm"] < nlm) | (sorts["lm"] == -1)).all(), "material sort lightmap out of range")
        nidx = s(0x4F) // 2
        need(((meshes["fi"].astype(np.int64) + 3 * meshes["tri"]) <= nidx).all(), "mesh indices out of range")
        need((meshes["fade"] == 0).all(), "mesh fade distance should be 0 (TF2 luxel data leaked?)")
        vcount = {0: s(0x48) // 28, 1: s(0x49) // 32, 2: s(0x47) // 20, 3: s(0x4A) // 24}
        vt = (meshes["flags"] >> 9) & 3
        bad = 0
        for k in range(len(meshes)):
            srt = sorts[meshes["ms"][k]]
            last = int(srt["fv"]) + int(meshes["fv"][k]) + int(meshes["lv"][k])
            if last >= vcount[{0: 0, 1: 1, 2: 2, 3: 3}[int(vt[k])]]:
                bad += 1
        need(bad == 0, "%d meshes reference vertices past their vertex lump" % bad)
        nrm = s(0x1E) // 12
        for lump, stride, tang in ((0x47, 20, None), (0x48, 28, None), (0x49, 32, 28), (0x4A, 24, 20)):
            a = np.frombuffer(m.raw(lump), "<u4").reshape(-1, stride // 4) if s(lump) else None
            if a is None:
                continue
            need((a[:, 0] < nverts).all(), "vertex lump %02x position index out of range" % lump)
            need((a[:, 1] < nrm).all(), "vertex lump %02x normal index out of range" % lump)
            if tang is not None:
                need(((a[:, tang // 4] & 0x7FFFFFFF) < nrm).all(), "vertex lump %02x tangent index out of range" % lump)

    for e in errs:
        print("FAIL", e)
    print("%s v%d flags %d: %s" % (os.path.basename(m.path), m.version, m.flags, "PASS" if not errs else "%d failures" % len(errs)))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

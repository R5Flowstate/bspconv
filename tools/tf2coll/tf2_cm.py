"""Titanfall 2 (rBSP v37) collision reader.

Reads the CM_* grid, brushes, tricoll meshes and static-prop primitives
straight from the lump files and turns every primitive into world-space
geometry. No bsp_tool dependency: every struct is unpacked here so the
layout assumptions live in one place and can be checked against data.
"""
import os
import struct
from dataclasses import dataclass, field

import numpy as np

LUMP_ENTITIES = 0x00
LUMP_PLANES = 0x01
LUMP_TEXTURE_DATA = 0x02
LUMP_VERTICES = 0x03
LUMP_MODELS = 0x0E
LUMP_GAME_LUMP = 0x23
LUMP_TEXTURE_DATA_STRING_DATA = 0x2B
LUMP_TEXTURE_DATA_STRING_TABLE = 0x2C
LUMP_TRICOLL_TRIANGLES = 0x42
LUMP_TRICOLL_NODES = 0x44
LUMP_TRICOLL_HEADERS = 0x45
LUMP_MESH_INDICES = 0x4F
LUMP_MESHES = 0x50
LUMP_MATERIAL_SORTS = 0x52
LUMP_VERTEX_UNLIT = 0x47
LUMP_VERTEX_LIT_FLAT = 0x48
LUMP_VERTEX_LIT_BUMP = 0x49
LUMP_VERTEX_UNLIT_TS = 0x4A
LUMP_CM_GRID = 0x55
LUMP_CM_GRID_CELLS = 0x56
LUMP_CM_GEO_SETS = 0x57
LUMP_CM_GEO_SET_BOUNDS = 0x58
LUMP_CM_PRIMITIVES = 0x59
LUMP_CM_PRIMITIVE_BOUNDS = 0x5A
LUMP_CM_UNIQUE_CONTENTS = 0x5B
LUMP_CM_BRUSHES = 0x5C
LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS = 0x5D
LUMP_CM_BRUSH_SIDE_PROPERTIES = 0x5E
LUMP_CM_BRUSH_SIDE_TEXTURE_VECTORS = 0x5F

PRIM_BRUSH = 0
PRIM_TRICOLL = 64
PRIM_PROP = 96


class Bsp:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            hdr = f.read(16 + 128 * 16)
        magic, self.version, self.flags, self.revision, _ = struct.unpack_from("<4sHHII", hdr, 0)
        if magic != b"rBSP":
            raise ValueError("not an rBSP: %r" % magic)
        self.lumps = [struct.unpack_from("<4I", hdr, 16 + 16 * i) for i in range(128)]
        self._cache = {}

    def raw(self, idx):
        if idx in self._cache:
            return self._cache[idx]
        side = "%s.%04x.bsp_lump" % (self.path, idx)
        if os.path.isfile(side):
            data = open(side, "rb").read()
        else:
            ofs, length, _, _ = self.lumps[idx]
            with open(self.path, "rb") as f:
                f.seek(ofs)
                data = f.read(length)
        self._cache[idx] = data
        return data

    def arr(self, idx, dtype):
        data = self.raw(idx)
        dt = np.dtype(dtype)
        n = len(data) // dt.itemsize
        if n * dt.itemsize != len(data):
            raise ValueError("lump 0x%02x size %d not a multiple of %d" % (idx, len(data), dt.itemsize))
        return np.frombuffer(data, dt, n)


DT_PLANE = np.dtype([("n", "<f4", 3), ("d", "<f4")])
DT_MODEL = np.dtype([("mins", "<f4", 3), ("maxs", "<f4", 3), ("first_mesh", "<u4"), ("num_meshes", "<u4")])
DT_TEXDATA = np.dtype([("refl", "<f4", 3), ("name", "<i4"), ("w", "<i4"), ("h", "<i4"),
                       ("vw", "<i4"), ("vh", "<i4"), ("flags", "<i4")])
DT_GRID = np.dtype([("scale", "<f4"), ("ox", "<i4"), ("oy", "<i4"), ("cx", "<i4"), ("cy", "<i4"),
                    ("straddle", "<i4"), ("first_brush_plane", "<i4")])
DT_GRIDCELL = np.dtype([("first", "<u2"), ("count", "<u2")])
DT_GEOSET = np.dtype([("straddle", "<u2"), ("count", "<u2"), ("prim", "<u4")])
DT_BOUNDS = np.dtype([("origin", "<i2", 3), ("b0", "u1"), ("b1", "u1"), ("extents", "<i2", 3), ("b2", "u1"), ("b3", "u1")])
DT_BRUSH = np.dtype([("origin", "<f4", 3), ("non_axial_no_discard", "u1"), ("num_planes", "u1"),
                     ("index", "<u2"), ("extents", "<f4", 3), ("side_offset", "<i4")])
DT_TRICOLL_HDR = np.dtype([("flags", "<i2"), ("tex_flags", "<i2"), ("texdata", "<i2"), ("num_verts", "<i2"),
                           ("num_tris", "<i2"), ("num_bevel", "<i2"), ("first_vert", "<i4"), ("first_tri", "<i4"),
                           ("first_node", "<i4"), ("first_bevel", "<i4"), ("origin", "<f4", 3), ("scale", "<f4")])


def prim_fields(v):
    v = int(v)
    return (v >> 24) & 0xFF, (v >> 8) & 0xFFFF, v & 0xFF


@dataclass
class Prim:
    kind: int
    index: int
    contents_idx: int
    bounds: tuple  # (mins, maxs) from the CM bounds record
    model: int


@dataclass
class Hull:
    """A brush: convex volume bounded by planes, with per-side surface texdata."""
    planes: list          # [(normal(3), dist)] -- outward, n.x <= d inside
    side_texdata: list    # parallel to planes
    side_flags: list
    verts: np.ndarray = None
    faces: list = field(default_factory=list)   # [(vert index loop, side index)]


class TF2Collision:
    def __init__(self, bsp_path):
        self.bsp = Bsp(bsp_path)
        b = self.bsp
        if b.version != 37:
            raise ValueError("expected rBSP v37, got %d" % b.version)
        self.planes = b.arr(LUMP_PLANES, DT_PLANE)
        self.verts = b.arr(LUMP_VERTICES, "<3f4")
        self.models = b.arr(LUMP_MODELS, DT_MODEL)
        self.texdata = b.arr(LUMP_TEXTURE_DATA, DT_TEXDATA)
        self.grid = b.arr(LUMP_CM_GRID, DT_GRID)[0]
        self.cells = b.arr(LUMP_CM_GRID_CELLS, DT_GRIDCELL)
        self.geosets = b.arr(LUMP_CM_GEO_SETS, DT_GEOSET)
        self.geoset_bounds = b.arr(LUMP_CM_GEO_SET_BOUNDS, DT_BOUNDS)
        self.prims = b.arr(LUMP_CM_PRIMITIVES, "<u4")
        self.prim_bounds = b.arr(LUMP_CM_PRIMITIVE_BOUNDS, DT_BOUNDS)
        self.unique_contents = b.arr(LUMP_CM_UNIQUE_CONTENTS, "<u4")
        self.brushes = b.arr(LUMP_CM_BRUSHES, DT_BRUSH)
        self.side_plane_ofs = b.arr(LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS, "<u2")
        self.side_props = b.arr(LUMP_CM_BRUSH_SIDE_PROPERTIES, "<u2")
        self.tri_hdrs = b.arr(LUMP_TRICOLL_HEADERS, DT_TRICOLL_HDR)
        self.tri_tris = b.arr(LUMP_TRICOLL_TRIANGLES, "<u4")
        sd = b.raw(LUMP_TEXTURE_DATA_STRING_DATA)
        st = b.arr(LUMP_TEXTURE_DATA_STRING_TABLE, "<i4")
        self.texnames = [sd[o:sd.index(b"\0", o)].decode("ascii", "replace") for o in st]

    def texdata_name(self, i):
        return self.texnames[self.texdata[i]["name"]]

    # --- grid -----------------------------------------------------------------

    def bounds_of(self, rec):
        o = rec["origin"].astype(np.float64)
        e = rec["extents"].astype(np.float64)
        return o - e, o + e

    def model_cells(self):
        """-> list over models of grid-cell index ranges.

        Worldspawn owns the first cx*cy cells, then one extra (empty) cell,
        then exactly one cell per brush model.
        """
        g = self.grid
        n = int(g["cx"]) * int(g["cy"])
        out = [list(range(0, n + 1))]
        for m in range(1, len(self.models)):
            out.append([n + m])
        return out

    def primitives(self):
        for prim, _ in self.primitives_with_bounds():
            yield prim

    def primitives_with_bounds(self):
        """Yield every primitive once, tagged with its model, deduplicated.

        A geoset with count==1 stores the primitive inline; otherwise its prim
        field indexes a run in CM_PRIMITIVES. Straddling geosets appear in
        several cells, so dedupe on (kind, index, contents).
        """
        seen = set()
        for model, cells in enumerate(self.model_cells()):
            for ci in cells:
                if ci >= len(self.cells):
                    continue
                c = self.cells[ci]
                for gi in range(int(c["first"]), int(c["first"]) + int(c["count"])):
                    gs = self.geosets[gi]
                    if int(gs["count"]) == 1:
                        kind, idx, cont = prim_fields(gs["prim"])
                        key = (model, kind, idx, cont)
                        if key not in seen:
                            seen.add(key)
                            rec = self.geoset_bounds[gi]
                            yield Prim(kind, idx, cont, self.bounds_of(rec), model), rec
                    else:
                        _, first, _ = prim_fields(gs["prim"])
                        for pi in range(first, first + int(gs["count"])):
                            kind, idx, cont = prim_fields(self.prims[pi])
                            key = (model, kind, idx, cont)
                            if key not in seen:
                                seen.add(key)
                                rec = self.prim_bounds[pi]
                                yield Prim(kind, idx, cont, self.bounds_of(rec), model), rec

    # --- brushes --------------------------------------------------------------

    def brush_hull(self, bi):
        br = self.brushes[bi]
        o = br["origin"].astype(np.float64)
        e = br["extents"].astype(np.float64)
        planes = [
            ((1.0, 0.0, 0.0), o[0] + e[0]), ((-1.0, 0.0, 0.0), -(o[0] - e[0])),
            ((0.0, 1.0, 0.0), o[1] + e[1]), ((0.0, -1.0, 0.0), -(o[1] - e[1])),
            ((0.0, 0.0, 1.0), o[2] + e[2]), ((0.0, 0.0, -1.0), -(o[2] - e[2])),
        ]
        g = self.grid
        for i in range(int(br["num_planes"])):
            off = int(br["side_offset"]) + i
            pidx = int(g["first_brush_plane"]) + off - int(self.side_plane_ofs[off])
            p = self.planes[pidx]
            planes.append((tuple(float(x) for x in p["n"]), float(p["d"])))
        start = 6 * int(br["index"]) + int(br["side_offset"])
        props = self.side_props[start:start + len(planes)]
        tex = [int(p) & 0x1FF for p in props]
        flags = [int(p) >> 9 for p in props]
        return Hull(planes, tex, flags)

    # --- tricoll --------------------------------------------------------------

    def tricoll_triangles(self, hi):
        h = self.tri_hdrs[hi]
        t = self.tri_tris[int(h["first_tri"]):int(h["first_tri"]) + int(h["num_tris"])].astype(np.int64)
        a = t & 0x3FF
        b = (t >> 10) & 0x7F
        c = (t >> 17) & 0x7F
        fl = (t >> 24) & 0xFF
        fv = int(h["first_vert"])
        idx = np.stack([fv + a, fv + a + b, fv + a + c], axis=1)
        return idx, fl


def hull_from_planes(planes, eps=1e-3):
    """Vertices and face loops of the convex volume {x : n.x <= d for all planes}.

    Faces come back as (vertex-index loop wound counter-clockwise seen from
    outside, plane index). Planes that do not touch the volume get no face.
    """
    n = np.array([p[0] for p in planes], dtype=np.float64)
    d = np.array([p[1] for p in planes], dtype=np.float64)
    pts = []
    k = len(planes)
    for i in range(k):
        for j in range(i + 1, k):
            for l in range(j + 1, k):
                m = np.stack([n[i], n[j], n[l]])
                det = np.linalg.det(m)
                if abs(det) < 1e-9:
                    continue
                x = np.linalg.solve(m, np.array([d[i], d[j], d[l]]))
                if (n @ x - d <= eps).all():
                    pts.append(x)
    if not pts:
        return np.zeros((0, 3)), []
    verts = []
    for p in pts:
        for q in verts:
            if np.abs(q - p).max() < eps * 10:
                break
        else:
            verts.append(p)
    verts = np.array(verts)
    faces = []
    for pi in range(k):
        on = np.nonzero(np.abs(verts @ n[pi] - d[pi]) < eps * 10)[0]
        if len(on) < 3:
            continue
        c = verts[on].mean(axis=0)
        u = verts[on[0]] - c
        u /= np.linalg.norm(u)
        w = np.cross(n[pi], u)
        ang = np.arctan2((verts[on] - c) @ w, (verts[on] - c) @ u)
        loop = [int(on[i]) for i in np.argsort(ang)]
        faces.append((loop, pi))
    return verts, faces

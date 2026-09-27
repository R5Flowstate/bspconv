"""Apex rBSP BVH4 collision writer (v47 dedi / v51 client share the format).

Items are convex hulls, float-vertex triangle sets and static-prop
references, each carrying a contents index. The writer builds a 4-wide tree,
quantises child bounds conservatively and packs the leaf stream the way the
engine's leaf handlers read it:

  node       int16 minMax[axis][min/max][slot] ; u32 meta[4]
             meta[i] >> 8 = child index (node or leaf dword offset)
             meta[0] & 0xFF = contents index gating the whole node
             meta[2]/meta[3] low bytes = child type nibbles
  poly leaf  u16 numPolys-1 << 12 | surfProp ; u16 baseVertex >> 10
             u32 per tri: dv0:11 | dv1-1:9 | dv2-1:9 | edgeFlags:3
  hull       u8 nVerts, nPlanes, nTriSets, nQuadSets ; float3 origin ; float scale
             int16 verts[n][3] ; u8 planes[n][3] ; pad4 ; tri-set poly leaves
  bundle     u32 count ; u32 desc[count] (contents:8 | type:8 | sizeDwords:16) ; payloads
  prop       u32 static prop index

Every leaf directly under a node inherits that node's contents index, so a
leaf whose contents differ from its parent is wrapped in a one-entry bundle.
"""
import math
import struct

import numpy as np

CHILD_NODE = 0
CHILD_EMPTY = 1
CHILD_BUNDLE = 3
CHILD_POLY = 4
CHILD_HULL = 8
CHILD_PROP = 9

EDGE_FLAGS_ALL = 7
MAX_POLYS_PER_LEAF = 16


def f32(x):
    return float(np.float32(x))


class Item:
    __slots__ = ("kind", "contents", "mins", "maxs", "data")

    def __init__(self, kind, contents, mins, maxs, data):
        self.kind = kind
        self.contents = contents
        self.mins = np.asarray(mins, dtype=np.float64)
        self.maxs = np.asarray(maxs, dtype=np.float64)
        self.data = data


class Writer:
    """Accumulates every model's tree into shared lump arrays."""

    def __init__(self, contents_masks=None):
        self.nodes = bytearray()
        self.leaf = []                 # u32 dwords
        self.verts = []                # float3 collision vertices
        self.contents_masks = list(contents_masks or [])
        self.surfprops = []            # (flags, surfType, contentsIdx, nameOfs)
        self._surfprop_index = {}
        self._contents_index = {m: i for i, m in enumerate(self.contents_masks)}

    # --- shared tables ------------------------------------------------------

    def contents_index(self, mask):
        i = self._contents_index.get(mask)
        if i is None:
            i = len(self.contents_masks)
            if i > 0xFF:
                raise ValueError("more than 256 distinct contents masks")
            self.contents_masks.append(mask)
            self._contents_index[mask] = i
        return i

    def surfprop_index(self, flags, surf_type, contents_idx, name_ofs):
        key = (flags & 0xFFFF, surf_type & 0xFF, contents_idx & 0xFF, name_ofs)
        i = self._surfprop_index.get(key)
        if i is None:
            i = len(self.surfprops)
            if i > 0xFFF:
                raise ValueError("more than 4096 surface properties")
            self.surfprops.append(key)
            self._surfprop_index[key] = i
        return i

    # --- item constructors --------------------------------------------------

    @staticmethod
    def tri_item(contents, surfprop, tris):
        """tris: float64 array (n,3,3), counter-clockwise seen from outside."""
        mins = tris.reshape(-1, 3).min(axis=0)
        maxs = tris.reshape(-1, 3).max(axis=0)
        return Item(CHILD_POLY, contents, mins, maxs, (surfprop, tris))

    @staticmethod
    def hull_item(contents, points, side_normals, side_surfprops):
        """Convex volume from its exact corner points.

        The hull is quantised to its own int16 grid first and rebuilt as the
        convex hull of the quantised points, so faces, plane triples and
        winding are consistent with what the engine will decode. Each face
        takes the surface of the source side whose normal it matches best.
        """
        from scipy.spatial import ConvexHull
        mn = points.min(axis=0)
        mx = points.max(axis=0)
        origin = np.array([f32(x) for x in (mn + mx) * 0.5])
        span = float(np.max(np.abs(points - origin)))
        step = 2.0 ** math.ceil(math.log2(max(span, 1e-6) / 32767.0))
        q = np.unique(np.rint((points - origin) / step).astype(np.int64), axis=0)
        world = origin + q.astype(np.float64) * step
        ch = ConvexHull(world)
        keep = sorted(set(int(i) for i in ch.vertices))
        remap = {old: new for new, old in enumerate(keep)}
        q = q[keep]
        world = world[keep]
        cen = world.mean(axis=0)
        faces = {}
        for simplex, eq in zip(ch.simplices, ch.equations):
            a, b, c = (remap[int(i)] for i in simplex)
            n = np.cross(world[b] - world[a], world[c] - world[a])
            if np.dot(n, eq[:3]) < 0:
                b, c = c, b
                n = -n
            key = (tuple(np.round(eq[:3], 5)), round(float(eq[3]), 3))
            faces.setdefault(key, []).append(((a, b, c), float(np.linalg.norm(n)), eq[:3]))
        planes = []
        sets = {}
        for tris in faces.values():
            tri, _, normal = max(tris, key=lambda t: t[1])
            planes.append(tri)
            side = int(np.argmax(side_normals @ normal))
            sets.setdefault(side_surfprops[side], []).extend(t[0] for t in tris)
        return Item(CHILD_HULL, contents, world.min(axis=0), world.max(axis=0),
                    (origin, step, q, planes, list(sets.items())))

    @staticmethod
    def prop_item(contents, prop_index, mins, maxs):
        return Item(CHILD_PROP, contents, mins, maxs, prop_index)

    # --- leaf encoders ------------------------------------------------------

    def _alloc_verts(self, pts):
        """Append positions (deduplicated within the call); return indices."""
        local = {}
        out = []
        for p in pts:
            key = (f32(p[0]), f32(p[1]), f32(p[2]))
            i = local.get(key)
            if i is None:
                i = len(self.verts)
                self.verts.append(key)
                local[key] = i
            out.append(i)
        return out

    @staticmethod
    def _encode_polys(surfprop, tris_idx, vert_base):
        """Pack one poly leaf. tris_idx are absolute indices into the model's
        vertex array; each is rotated so its smallest index leads."""
        rot = []
        for a, b, c in tris_idx:
            m = min(a, b, c)
            if m == b:
                a, b, c = b, c, a
            elif m == c:
                a, b, c = c, a, b
            rot.append((a, b, c))
        rot.sort(key=lambda t: t[0])
        if not rot:
            raise ValueError("empty poly leaf")
        base = (rot[0][0] - vert_base) >> 10
        if base > 0xFFFF:
            raise ValueError("vertex base out of range")
        header = ((len(rot) - 1) << 12) | (surfprop & 0xFFF) | (base << 16)
        out = [header]
        running = vert_base + (base << 10)
        for a, b, c in rot:
            d0 = a - running
            d1 = b - a - 1
            d2 = c - a - 1
            if not (0 <= d0 <= 0x7FF and 0 <= d1 <= 0x1FF and 0 <= d2 <= 0x1FF):
                raise ValueError("poly delta out of range (%d,%d,%d)" % (d0, d1, d2))
            out.append(d0 | (d1 << 11) | (d2 << 20) | (EDGE_FLAGS_ALL << 29))
            running = a
        return out

    def _encode_tri_leaves(self, surfprop, tris):
        """-> list of dword lists, one per poly leaf (<=16 tris each)."""
        leaves = []
        for s in range(0, len(tris), MAX_POLYS_PER_LEAF):
            chunk = tris[s:s + MAX_POLYS_PER_LEAF]
            idx = self._alloc_verts(chunk.reshape(-1, 3))
            tri_idx = [tuple(idx[3 * k:3 * k + 3]) for k in range(len(chunk))]
            leaves.append(self._encode_polys(surfprop, tri_idx, self._model_vert_base))
        return leaves

    def _encode_hull(self, origin, step, q, planes, face_sets):
        if len(q) > 0xFF or len(planes) > 0xFF:
            raise ValueError("hull too large: %d verts %d planes" % (len(q), len(planes)))
        if np.abs(q).max() > 32767:
            raise ValueError("hull quantisation overflow")
        verts = q
        scale = step / 65536.0
        head = struct.pack("<4B4f", len(verts), len(planes), 0, 0,
                           origin[0], origin[1], origin[2], scale)
        body = b"".join(struct.pack("<3h", *map(int, v)) for v in q)
        body += bytes(i for p in planes for i in p)
        blob = head + body
        blob += b"\0" * (-len(blob) & 3)
        dwords = list(struct.unpack("<%dI" % (len(blob) // 4), blob))
        tri_sets = 0
        for surfprop, tris in face_sets:
            for s in range(0, len(tris), MAX_POLYS_PER_LEAF):
                dwords += self._encode_polys(surfprop, tris[s:s + MAX_POLYS_PER_LEAF], 0)
                tri_sets += 1
        if tri_sets > 0xFF:
            raise ValueError("hull has more than 255 tri sets")
        dwords[0] = (dwords[0] & ~0x00FF0000) | (tri_sets << 16)
        return dwords

    def _encode_item(self, it):
        """-> list of (type, dwords) sub-leaves making up the item."""
        if it.kind == CHILD_PROP:
            return [(CHILD_PROP, [it.data & 0xFFFFFF])]
        if it.kind == CHILD_HULL:
            return [(CHILD_HULL, self._encode_hull(*it.data))]
        surfprop, tris = it.data
        return [(CHILD_POLY, d) for d in self._encode_tri_leaves(surfprop, tris)]

    # --- tree ---------------------------------------------------------------

    def build_model(self, items, origin, decode_step):
        """Encode one model. Returns the dmodel BVH fields."""
        if not items:
            # A model the engine may still query: one node, four empty slots.
            node_base = len(self.nodes) // 64
            meta = [self.contents_index(0), 0, CHILD_EMPTY | (CHILD_EMPTY << 4), CHILD_EMPTY | (CHILD_EMPTY << 4)]
            self.nodes.extend(bytes(48) + struct.pack("<4I", *meta))
            return dict(bvh_node=node_base, bvh_leaf=len(self.leaf), vert_index=len(self.verts), flags=0,
                        origin=tuple(f32(x) for x in origin), decode_scale=f32(decode_step / 65536.0), empty=False)
        self._model_vert_base = len(self.verts)
        node_base = len(self.nodes) // 64
        leaf_base = len(self.leaf)
        self._origin = np.asarray(origin, dtype=np.float64)
        self._step = decode_step

        # Split tri items to at most 16 tris per poly leaf first, so every
        # tree slot costs about the same to visit.
        flat = []
        for it in items:
            if it.kind == CHILD_POLY and len(it.data[1]) > MAX_POLYS_PER_LEAF:
                flat.extend(self._split_tris(it))
            else:
                flat.append(it)

        # Top-down build, breadth-first node numbering like stock.
        root = self._partition(flat)
        order = []
        queue = [root]
        while queue:
            n = queue.pop(0)
            order.append(n)
            for ch in n["children"]:
                if ch["type"] == "node":
                    queue.append(ch)
        for i, n in enumerate(order):
            n["index"] = i
        self.nodes.extend(bytes(64 * len(order)))
        for n in order:
            self._emit_node(n, node_base, leaf_base)
        return dict(bvh_node=node_base, bvh_leaf=leaf_base,
                    vert_index=self._model_vert_base, flags=0,
                    origin=tuple(f32(x) for x in self._origin),
                    decode_scale=f32(decode_step / 65536.0), empty=False)

    def _split_tris(self, it):
        surfprop, tris = it.data
        cen = tris.mean(axis=1)
        out = []

        def rec(sel):
            if len(sel) <= MAX_POLYS_PER_LEAF:
                t = tris[sel]
                out.append(Item(CHILD_POLY, it.contents, t.reshape(-1, 3).min(0),
                                t.reshape(-1, 3).max(0), (surfprop, t)))
                return
            c = cen[sel]
            ax = int(np.argmax(c.max(0) - c.min(0)))
            o = sel[np.argsort(c[:, ax], kind="stable")]
            h = len(o) // 2
            rec(o[:h])
            rec(o[h:])

        rec(np.arange(len(tris)))
        return out

    def _partition(self, items):
        mins = np.min([it.mins for it in items], axis=0)
        maxs = np.max([it.maxs for it in items], axis=0)
        mask = 0
        for it in items:
            mask |= self.contents_masks[it.contents]
        node = {"type": "node", "mins": mins, "maxs": maxs, "mask": mask, "children": []}
        if len(items) <= 4:
            groups = [[it] for it in items]
        else:
            groups = self._split4(items)
        for g in groups:
            if len(g) == 1:
                it = g[0]
                node["children"].append({"type": "item", "item": it, "mins": it.mins, "maxs": it.maxs})
            else:
                node["children"].append(self._partition(g))
        return node

    @staticmethod
    def _split2(items):
        c = np.array([(it.mins + it.maxs) * 0.5 for it in items])
        ax = int(np.argmax(c.max(0) - c.min(0)))
        order = np.argsort(c[:, ax], kind="stable")
        h = len(items) // 2
        return [items[i] for i in order[:h]], [items[i] for i in order[h:]]

    def _split4(self, items):
        a, b = self._split2(items)
        out = []
        for g in (a, b):
            if len(g) >= 2:
                out.extend(self._split2(g))
            else:
                out.append(g)
        return [g for g in out if g]

    def _quant(self, v, up):
        # Pad by a hair so float32 decode rounding never shrinks a bound.
        v = v + (1e-3 if up else -1e-3)
        q = (v - self._origin) / self._step
        q = np.ceil(q) if up else np.floor(q)
        return np.clip(q, -32768, 32767).astype(np.int64)

    def _emit_node(self, n, node_base, leaf_base):
        mm = np.zeros((3, 2, 4), dtype=np.int64)
        meta = [0, 0, 0, 0]
        types = [CHILD_EMPTY] * 4
        cidx = self.contents_index(n["mask"])
        for slot in range(4):
            if slot >= len(n["children"]):
                mm[:, 0, slot] = 0
                mm[:, 1, slot] = 0
                continue
            ch = n["children"][slot]
            mm[:, 0, slot] = self._quant(ch["mins"], False)
            mm[:, 1, slot] = self._quant(ch["maxs"], True)
            if ch["type"] == "node":
                types[slot] = CHILD_NODE
                meta[slot] = ch["index"] << 8
            else:
                it = ch["item"]
                subs = self._encode_item(it)
                if len(subs) == 1 and it.contents == cidx:
                    t, dw = subs[0]
                    off = len(self.leaf) - leaf_base
                    self.leaf.extend(dw)
                else:
                    t = CHILD_BUNDLE
                    off = len(self.leaf) - leaf_base
                    self.leaf.append(len(subs))
                    for st, dw in subs:
                        if len(dw) > 0xFFFF:
                            raise ValueError("bundle entry too large")
                        self.leaf.append((it.contents & 0xFF) | (st << 8) | (len(dw) << 16))
                    for st, dw in subs:
                        self.leaf.extend(dw)
                types[slot] = t
                if off > 0xFFFFFF:
                    raise ValueError("leaf offset out of range")
                meta[slot] = off << 8
        meta[0] |= cidx
        meta[2] |= types[0] | (types[1] << 4)
        meta[3] |= types[2] | (types[3] << 4)
        rec = struct.pack("<24h", *[int(mm[a, m, s]) for a in range(3) for m in range(2) for s in range(4)])
        rec += struct.pack("<4I", *meta)
        at = (node_base + n["index"]) * 64
        self.nodes[at:at + 64] = rec

    # --- lump bytes ---------------------------------------------------------

    def lump_nodes(self):
        return bytes(self.nodes)

    def lump_leaf(self):
        return struct.pack("<%dI" % len(self.leaf), *self.leaf)

    def lump_contents(self):
        return struct.pack("<%dI" % len(self.contents_masks), *self.contents_masks)

    def lump_surfprops(self):
        return b"".join(struct.pack("<HBBI", *s) for s in self.surfprops)

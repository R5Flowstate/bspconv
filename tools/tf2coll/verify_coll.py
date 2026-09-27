"""Prove a converted map's BVH4 collision equals the TF2 source, primitive by primitive.

    py verify_coll.py <tf2.bsp> <apex.bsp>

Decodes the Apex lumps exactly as the engine walks them (node contents gate,
per-entry bundle contents, poly/hull/prop leaf handlers) without sharing any
code with the writer, then checks:

  1. every TF2 tricoll triangle appears once, bit-exact, wound outward (CCW);
  2. every TF2 brush volume equals its hull leaf: same vertex set within the
     hull's quantisation step, every plane triple outward;
  3. the static-prop reference set equals the TF2 prop primitive set;
  4. the contents mask each leaf is filtered by equals its TF2 primitive's;
  5. every leaf lies inside the dequantised bounds of the slot referencing it;
  6. brush hulls agree with the TF2 plane set on inside/outside for random probes.
"""
import struct
import sys
from collections import Counter

import numpy as np

from tf2_cm import TF2Collision, PRIM_BRUSH, PRIM_TRICOLL, PRIM_PROP, hull_from_planes


def f32(x):
    return np.float32(x)


class ApexColl:
    def __init__(self, path):
        L = lambda i: open("%s.%04x.bsp_lump" % (path, i), "rb").read()
        self.verts = np.frombuffer(L(0x03), "<f4").reshape(-1, 3)
        self.models = L(0x0E)
        self.cmasks = np.frombuffer(L(0x10), "<u4")
        self.nodes = L(0x12)
        self.raw = L(0x13)
        self.leaf = np.frombuffer(self.raw, "<u4")

    def model(self, i):
        node, leaf, vert, flags = struct.unpack_from("<4i", self.models, i * 64 + 32)
        ox, oy, oz, sc = struct.unpack_from("<4f", self.models, i * 64 + 48)
        return node, leaf, vert, flags, np.array([ox, oy, oz], np.float32), np.float32(sc)

    def walk(self, mi):
        """Yield (leaf_type, dword_offset_abs, contents_mask, slot_mins, slot_maxs)."""
        node0, leaf0, vert0, flags, origin, sc = self.model(mi)
        if node0 < 0:
            return
        step = np.float32(sc) * np.float32(65536.0)
        stack = [0]
        seen = set()
        while stack:
            ni = stack.pop()
            if ni in seen:
                raise ValueError("node %d reached twice" % ni)
            seen.add(ni)
            base = (node0 + ni) * 64
            mm = np.array(struct.unpack_from("<24h", self.nodes, base), np.int64).reshape(3, 2, 4)
            meta = struct.unpack_from("<4I", self.nodes, base + 48)
            cmask = int(self.cmasks[meta[0] & 0xFF])
            types = [meta[2] & 0xF, (meta[2] >> 4) & 0xF, meta[3] & 0xF, (meta[3] >> 4) & 0xF]
            for s in range(4):
                t = types[s]
                ci = meta[s] >> 8
                smin = origin + (mm[:, 0, s] << 16).astype(np.float32) * sc
                smax = origin + (mm[:, 1, s] << 16).astype(np.float32) * sc
                if t == 0:
                    stack.append(ci)
                elif t == 1:
                    continue
                elif t == 3:
                    o = leaf0 + ci
                    cnt = int(self.leaf[o])
                    pay = o + 1 + cnt
                    for k in range(cnt):
                        d = int(self.leaf[o + 1 + k])
                        yield (d >> 8) & 0xFF, pay, int(self.cmasks[d & 0xFF]), smin, smax
                        pay += d >> 16
                else:
                    yield t, leaf0 + ci, cmask, smin, smax

    def poly_tris(self, o, vert_base, local=None):
        h = int(self.leaf[o])
        n = ((h >> 12) & 0xF) + 1
        surf = h & 0xFFF
        run = (h >> 16) << 10
        out = []
        for p in range(n):
            pk = int(self.leaf[o + 1 + p])
            run += pk & 0x7FF
            idx = (run, run + ((pk >> 11) & 0x1FF) + 1, run + ((pk >> 20) & 0x1FF) + 1)
            if pk >> 29 != 7:
                raise ValueError("edge flags %d" % (pk >> 29))
            if local is not None:
                out.append(np.array([local[i] for i in idx]))
            else:
                out.append(self.verts[[vert_base + i for i in idx]])
        return out, surf, 1 + n

    def hull(self, o):
        b = o * 4
        nv, npl, nts, nqs = self.raw[b], self.raw[b + 1], self.raw[b + 2], self.raw[b + 3]
        ox, oy, oz, sc = struct.unpack_from("<4f", self.raw, b + 4)
        q = np.array(struct.unpack_from("<%dh" % (3 * nv), self.raw, b + 20), np.int64).reshape(-1, 3)
        V = np.array([ox, oy, oz], np.float32) + (q << 16).astype(np.float32) * np.float32(sc)
        pl = np.frombuffer(self.raw, np.uint8, 3 * npl, b + 20 + 6 * nv).reshape(-1, 3)
        off = o + (20 + 6 * nv + 3 * npl + 3) // 4
        tris = []
        for k in range(nts + nqs):
            if k >= nts:
                raise ValueError("quad sets not expected")
            t, _, used = self.poly_tris(off, 0, V)
            tris += t
            off += used
        return V.astype(np.float64), pl, tris, np.float64(sc) * 65536.0


def tri_key(t):
    t = np.asarray(t, np.float32)
    rows = [tuple(r) for r in t]
    i = min(range(3), key=lambda k: rows[k])
    return tuple(rows[i:] + rows[:i])


def main(argv):
    tf2 = TF2Collision(argv[0])
    ax = ApexColl(argv[1])
    fails = Counter()

    want_tris = Counter()
    want_prop = set()
    want_hulls = {}
    prim_contents = {}
    for prim in tf2.primitives():
        cm = int(tf2.unique_contents[prim.contents_idx])
        if prim.kind == PRIM_TRICOLL:
            idx, _ = tf2.tricoll_triangles(prim.index)
            for t in tf2.verts[idx]:
                t = t[[0, 2, 1]]
                want_tris[(prim.model, tri_key(t), cm)] += 1
        elif prim.kind == PRIM_PROP:
            want_prop.add((prim.model, prim.index, cm))
        elif prim.kind == PRIM_BRUSH:
            want_hulls.setdefault(prim.model, []).append((prim.index, cm))

    got_tris = Counter()
    got_prop = set()
    got_hulls = {}
    outside_slot = 0
    for mi in range(len(tf2.models)):
        _, _, vert0, flags, _, _ = ax.model(mi)
        for t, o, cm, smin, smax in ax.walk(mi):
            lo = smin.astype(np.float64) - 1e-3
            hi = smax.astype(np.float64) + 1e-3
            if t == 4:
                tris, _, _ = ax.poly_tris(o, vert0)
                for tr in tris:
                    if (tr < lo).any() or (tr > hi).any():
                        outside_slot += 1
                    got_tris[(mi, tri_key(tr), cm)] += 1
            elif t == 9:
                got_prop.add((mi, int(ax.leaf[o]) & 0xFFFFFF, cm))
            elif t == 8:
                V, pl, tris, step = ax.hull(o)
                if (V < lo).any() or (V > hi).any():
                    outside_slot += 1
                got_hulls.setdefault(mi, []).append((V, pl, tris, step, cm))
            else:
                fails["unexpected leaf type %d" % t] += 1

    missing = want_tris - got_tris
    extra = got_tris - want_tris
    print("[tris] want %d  got %d  missing %d  extra %d"
          % (sum(want_tris.values()), sum(got_tris.values()), sum(missing.values()), sum(extra.values())))
    print("[props] want %d  got %d  equal %s" % (len(want_prop), len(got_prop), want_prop == got_prop))
    print("[slot] leaves outside their slot bounds: %d" % outside_slot)

    # Hulls: match each TF2 brush to a decoded hull by contents + vertex set.
    rng = np.random.default_rng(1)
    hull_ok = hull_bad = probe_bad = probes = 0
    wind_bad = 0
    for mi, lst in want_hulls.items():
        pool = got_hulls.get(mi, [])
        used = [False] * len(pool)
        for bi, cm in lst:
            h = tf2.brush_hull(bi)
            v, _ = hull_from_planes(h.planes)
            match = None
            best = None
            vmin, vmax = v.min(axis=0), v.max(axis=0)
            N = np.array([p[0] for p in h.planes])
            D = np.array([p[1] for p in h.planes])
            for k, (V, pl, tris, step, gcm) in enumerate(pool):
                if used[k] or gcm != cm:
                    continue
                d = max(np.abs(V.min(axis=0) - vmin).max(), np.abs(V.max(axis=0) - vmax).max())
                if d <= step + 2e-3 and (V @ N.T - D).max() <= step + 2e-3 and (best is None or d < best):
                    best = d
                    match = k
            if match is None:
                hull_bad += 1
                continue
            used[match] = True
            hull_ok += 1
            V, pl, tris, step, _ = pool[match]
            cen = V.mean(axis=0)
            for a, b, c in pl:
                n = np.cross(V[b] - V[a], V[c] - V[a])
                if np.dot(cen - V[a], n) >= 0:
                    wind_bad += 1
            for tr in tris:
                n = np.cross(tr[1] - tr[0], tr[2] - tr[0])
                if np.dot(tr[0] - cen, n) <= 0:
                    wind_bad += 1
            # Inside/outside agreement on random probes around the brush.
            mn, mx = v.min(axis=0), v.max(axis=0)
            pad = (mx - mn) * 0.1 + 1.0
            P = rng.uniform(mn - pad, mx + pad, size=(256, 3))
            N = np.array([p[0] for p in h.planes])
            D = np.array([p[1] for p in h.planes])
            inside_tf2 = ((P @ N.T - D) <= 0).all(axis=1)
            Np = np.array([np.cross(V[b] - V[a], V[c] - V[a]) for a, b, c in pl])
            Np /= np.linalg.norm(Np, axis=1)[:, None]
            Dp = np.array([np.dot(Np[i], V[pl[i][0]]) for i in range(len(pl))])
            dist_tf2 = np.max(P @ N.T - D, axis=1)
            inside_ax = ((P @ Np.T - Dp) <= 0).all(axis=1)
            # Probes within quantisation distance of the surface may flip.
            decisive = np.abs(dist_tf2) > step * 2
            probes += int(decisive.sum())
            probe_bad += int((inside_tf2 != inside_ax)[decisive].sum())
    print("[hulls] matched %d  unmatched %d  winding errors %d  probe disagreements %d / %d"
          % (hull_ok, hull_bad, wind_bad, probe_bad, probes))
    for k, v in fails.items():
        print("[fail]", k, v)
    ok = (not missing and not extra and want_prop == got_prop and outside_slot == 0
          and hull_bad == 0 and wind_bad == 0 and probe_bad == 0 and not fails)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Add the character select menu instance to a converted client map.

    py menu_instance.py <converted dir> <map name> [--origin x y z]

Every S21 map carries the client-side targets character select looks up by
script name (camera, pilot mark, background mark). Without them the menu
script errors out. Placement defaults to open air above the level: the
centre of the nearest 2560-unit cube that holds no world vertex.
"""
import argparse
import os

import numpy as np

CELL = 512
REACH = 2  # cells of clearance on each side

# Offsets from the instance origin, as the stock menu box lays them out.
TARGETS = (
    ("target_char_sel_camera_new", (0, -94, 21), "0 90 0"),
    ("target_char_sel_pilot_new", (-16, 14, -27), "0 -90 0"),
    ("target_char_sel_bg_new", (0, 152, -30), "0 90 0"),
)


def find_origin(verts):
    lo = verts.min(0)
    n = np.ceil((verts.max(0) - lo) / CELL).astype(int) + 1
    idx = ((verts - lo) // CELL).astype(int)
    occ = np.zeros(n, bool)
    occ[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    busy = occ.copy()
    for ax in range(3):
        grown = busy.copy()
        for s in range(1, REACH + 1):
            grown |= np.roll(busy, s, ax) | np.roll(busy, -s, ax)
        busy = grown
    free = np.argwhere(~busy)
    free = free[np.all((free >= REACH) & (free < n - REACH), 1)]
    if not len(free):
        raise SystemExit("no open space for the menu instance; pass --origin")
    centre = lo + (free + 0.5) * CELL
    top = np.percentile(verts[:, 2], 99)
    above = centre[centre[:, 2] > top]
    pool = above if len(above) else centre
    mid = np.median(verts, 0)
    return pool[np.argmin(np.linalg.norm((pool - mid)[:, :2], axis=1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("conv")
    ap.add_argument("name")
    ap.add_argument("--origin", nargs=3, type=float)
    a = ap.parse_args()

    path = os.path.join(a.conv, "%s_script.ent" % a.name)
    ent = open(path, "rb").read().decode("latin-1")
    if "target_char_sel_camera_new" in ent:
        print("[menu] instance already present")
        return
    if a.origin:
        origin = np.array(a.origin)
    else:
        verts = np.fromfile(os.path.join(a.conv, "%s.bsp.0003.bsp_lump" % a.name), "<f4").reshape(-1, 3)
        origin = find_origin(verts)

    blocks = []
    for script_name, ofs, angles in TARGETS:
        p = origin + ofs
        blocks.append('{\n"scale" "1"\n"angles" "%s"\n"origin" "%g %g %g"\n"script_name" "%s"\n'
                      '"classname" "info_target_clientside"\n}\n' % (angles, p[0], p[1], p[2], script_name))
    tail = len(ent) - len(ent.rstrip("\0"))
    ent = ent.rstrip("\0") + "".join(blocks) + "\0" * tail
    open(path, "wb").write(ent.encode("latin-1"))
    print("[menu] instance at %g %g %g -> %s" % (origin[0], origin[1], origin[2], path))


if __name__ == "__main__":
    main()

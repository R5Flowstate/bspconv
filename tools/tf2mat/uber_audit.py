"""Report TF2 uber members that land nowhere in the S21 twin buffer.

    py uber_audit.py <config.json>

A TF2 member with no S21 home is a value the conversion silently drops;
the list must be empty. S21 members filled from the set's stock value are
listed for review.
"""
import collections
import json
import re
import sys

import tf2_matl_to_s21 as t


def main(argv):
    cfg = json.load(open(argv[0]))
    c = t.Converter(cfg)
    left = collections.Counter()
    lost = collections.Counter()
    rev = {"wldc": "wld", "rgdp": "fix", "sknp": "skn"}
    for w in (l.strip() for l in open(cfg["wanted"]) if l.strip()):
        name, typ = w.rsplit("_", 1)
        src = c.tf2.get(t.mat_guid(name, rev[typ]))
        if not src:
            continue
        tw = c.twin(t.g(src["shaderSet"]))
        tl = c.shaders.uber_layout(c.shaders.set_ps(t.g(src["shaderSet"])) or 0)
        sl = c.shaders.uber_layout(c.shaders.set_ps(tw[0]) or 0) if tw else None
        if not tl or not sl:
            continue
        used = set()
        for n, _, s in sl[1]:
            r = t.tf2_value_for(n, tl[1], bytes(tl[0]), s)
            if r is None:
                left[n] += 1
                continue
            how = r[1]
            if how == "same":
                used.add(n)
            elif how == "layer0":
                used.add(n.replace("c_L0_", "c_"))
            elif how == "component":
                used.add("c_" + n.replace("c_L0_", "")[:-1])
            else:
                for p in re.match(r"c_(\w+?)And(\w+)$", n).groups():
                    if not p.startswith("L1_"):
                        q = p.replace("L0_", "", 1)
                        used.add("c_" + t.RENAMED.get(q, q))
        for n, _, _ in tl[1]:
            if n not in used and not n.startswith("pad"):
                lost[n] += 1
    print("TF2 members with no S21 home:", lost.most_common())
    print("S21 members from the stock set value:", [n for n, _ in left.most_common()])
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

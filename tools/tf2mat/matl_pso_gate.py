"""Gate converted material JSONs against the S21 PSO-key contract.

    py matl_pso_gate.py <assets_client/material dir>

matl+0xB8 (Repak "dxStateUnk28", 8 bytes) is u16 renderPassFormats + u16 topo:
the dx12 PSO key indexes the render-pass table with it unchecked. Stock
(7,057 materials, staging_mu1 + district): main/colpass/prepass 0, shadow and
tightshadow 2, vsm 3, upper bytes 0. Also rejects an enabled blend on slot 3
(R32_UINT in the 7-target pass), any blend array outside the stock families
(translucent canon, depth-only 0 x8, else F0000000 x8) and a translucent that
writes depth (depthStencilFlags bit 4). Exit 1 if anything fails.
"""
import glob, json, os, re, sys

def role(name):
    if name.endswith("_colpass"):
        return "colpass"
    m = re.search(r"_(prepass|shadow|tightshadow|vsm)$", name)
    return m.group(1) if m else "main"

OPAQUE = [0xF0000000] * 8
DEPTH_ONLY = [0] * 8
DEPTH_WRITE = 0x10

WANT = {"main": 0, "colpass": 0, "prepass": 0, "shadow": 2, "tightshadow": 2, "vsm": 3}

def main(root):
    bad = warn = n = 0
    for p in sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True)):
        try:
            j = json.load(open(p))
        except Exception:
            continue
        if "dxStateUnk28" not in j:
            continue
        n += 1
        v = int(j["dxStateUnk28"], 16)
        rpf, topo, hi = v & 0xFFFF, (v >> 16) & 0xFFFF, v >> 32
        want = WANT[role(j["name"])]
        if rpf > 3 or topo or hi:
            bad += 1
            print("FAIL  %-70s dxStateUnk28=0x%X (rpf %d topo %d) -> want 0x%X" % (j["name"], v, rpf, topo, want))
        elif rpf != want:
            warn += 1
            print("WARN  %-70s rpf %d, stock %s uses %d" % (j["name"], rpf, role(j["name"]), want))
        b = [int(x, 16) for x in j.get("blendStates", [])]
        if len(b) > 3 and b[3] & 2:
            bad += 1
            print("FAIL  %-70s blendStates[3] enables blend on R32_UINT" % j["name"])
        if b:
            translucent = any(x & 2 for x in b)
            family = DEPTH_ONLY if role(j["name"]) in ("prepass", "shadow", "tightshadow") else OPAQUE
            if not translucent and b != family:
                bad += 1
                print("FAIL  %-70s blendStates %s, stock %s is %s" % (j["name"], ["%X" % x for x in b], role(j["name"]), "%X x8" % family[0]))
            if translucent and int(j.get("depthStencilFlags", "0"), 16) & DEPTH_WRITE:
                bad += 1
                print("FAIL  %-70s translucent with depth write (depthStencilFlags %s)" % (j["name"], j["depthStencilFlags"]))
    print("materials %d  fail %d  warn %d" % (n, bad, warn))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

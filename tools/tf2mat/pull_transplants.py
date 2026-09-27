"""Export every asset gen_client_manifest reports for transplant from its S21 pak,
then rerun the manifest until the closure has nothing left to pull.

    py pull_transplants.py <client_cfg.json> <s21 paks dir> [rsx.exe]
"""
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
import paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = {"mdl_": "mdl_", "txtr": "txtr", "shdr": "shdr", "shds": "shds", "matl": "matl"}


def children_of(adj):
    """child shader guid -> parent guid from an RSX depfile (child lines name two shaders)."""
    out = {}
    for line in open(adj, encoding="utf-8", errors="replace"):
        parts = line.strip().split(",")
        if len(parts) == 2 and all(p.startswith("shader\\0x") for p in parts):
            c, pg = (int(p.split("0x")[1].split(".")[0], 16) for p in parts)
            out[c] = pg
    return out


def main(argv):
    cfg_path, paks = argv[0], argv[1]
    rsx = argv[2] if len(argv) > 2 else paths.get("rsxExe")
    cfg = json.load(open(cfg_path))
    out = cfg["transplant_dir"]
    for rnd in range(1, 10):
        subprocess.run([sys.executable, os.path.join(HERE, "gen_client_manifest.py"), cfg_path], check=True)
        pending = json.load(open(cfg["manifest"] + ".transplant.json"))
        if not pending:
            print("closure complete after %d round(s)" % (rnd - 1))
            return 0
        by_pak = collections.defaultdict(list)
        for g, (typ, pak, _) in pending.items():
            by_pak[pak].append((typ, g))
        # RSX never exports child shaders (no bytecode); Repak rebuilds them from the parent.
        added = False
        for pak, items in by_pak.items():
            if not any(t == "shdr" for t, _ in items):
                continue
            adj = os.path.join(cfg.get("adj_dir", out), pak + ".adj")
            if not os.path.isfile(adj):
                subprocess.run([rsx, "-nogui", "--depfilepath", adj, os.path.join(paks, pak + ".rpak")],
                               cwd=os.path.dirname(rsx), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            kids = children_of(adj) if os.path.isfile(adj) else {}
            for t, g in items:
                if t == "shdr" and int(g, 16) in kids:
                    cfg.setdefault("child_shaders", {})["0x" + g] = "0x%016X" % kids[int(g, 16)]
                    added = True
        if added:
            json.dump(cfg, open(cfg_path, "w"), indent=1)
        for pak, items in by_pak.items():
            gl = os.path.join(out, "_round%d_%s.txt" % (rnd, pak))
            open(gl, "w").write("\n".join("0x" + g for _, g in items))
            srcs = [os.path.join(paks, pak + ".rpak")]
            patch = os.path.join(paks, pak + "(01).rpak")
            if os.path.isfile(patch):
                srcs.append(patch)
            types = ",".join(sorted({TYPES[t] for t, _ in items}))
            subprocess.run([rsx, "-nogui", "-export", "-exportfullpaths", "--texturenames", "guid",
                            "--exportguids", gl, "--exporttypes", types, "--exportdir", out,
                            "--parsethreads", "8", "--exportthreads", "8"] + srcs,
                           cwd=os.path.dirname(rsx), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            print("round %d: %s %d (%s)" % (rnd, pak, len(items), types))
    print("closure did not converge")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Move entities into the partition an Apex map keeps them in.

    py entity_partitions.py <converted dir> <map name>

TF2 leaves some classes in the worldspawn entity lump that Apex ships in a
partition file. Placement is not cosmetic: the client builds partition
entities itself, while worldspawn entities are spawned by the server and
networked, so a class the client expects to own locally (fog_volume resolves
its brush model client side) breaks when the server owns it instead.
"""
import os
import re
import struct
import sys

# classname -> partition, as stock S21 maps place them.
PLACEMENT = {"fog_volume": "env"}


def retarget_color_correction(conv, name):
    """S21 loads a grade from the mod folders only through the .raw_hdr reader;
    TF2's 32^3 .raw goes to a loader that sees the base install alone. The
    volume itself is baked by tf2fx/cc_raw_hdr.py."""
    path = os.path.join(conv, "%s_env.ent" % name)
    if not os.path.exists(path):
        return
    ent = open(path, "rb").read().decode("latin-1")
    lut = "materials/correction/%s.raw_hdr" % name

    def repl(m):
        body = m.group(0)
        if '"classname" "color_correction"' not in body:
            return body
        src = re.search(r'"filename" "([^"]*)"', body)
        if not src or src.group(1).lower().endswith(".raw_hdr"):
            return body
        print("%s: color_correction %s -> %s (bake with tf2fx/cc_raw_hdr.py)" % (name, src.group(1), lut))
        return body.replace(src.group(0), '"filename" "%s"' % lut)

    out = re.sub(r"\{[^{}]*\}", repl, ent)
    if out != ent:
        open(path, "wb").write(out.encode("latin-1"))


def main(argv):
    conv, name = argv[0], argv[1]
    lump = os.path.join(conv, "%s.bsp.0000.bsp_lump" % name)
    text = open(lump, "rb").read().decode("latin-1")
    moved = {}

    def repl(m):
        body = m.group(0)
        cl = re.search(r'"classname" "([^"]*)"', body)
        part = PLACEMENT.get(cl.group(1)) if cl else None
        if not part:
            return body
        moved.setdefault(part, []).append(body)
        return ""

    text = re.sub(r"\{[^{}]*\}\n?", repl, text)
    for part, bodies in moved.items():
        path = os.path.join(conv, "%s_%s.ent" % (name, part))
        ent = open(path, "rb").read().decode("latin-1")
        tail = len(ent) - len(ent.rstrip("\0"))
        ent = ent.rstrip("\0")
        if not ent.endswith("\n"):
            ent += "\n"
        ent += "".join(b if b.endswith("\n") else b + "\n" for b in bodies) + "\0" * tail
        open(path, "wb").write(ent.encode("latin-1"))
        print("%s: %d entities -> %s" % (name, len(bodies), os.path.basename(path)))
    retarget_color_correction(conv, name)
    data = text.encode("latin-1")
    open(lump, "wb").write(data)
    bsp = os.path.join(conv, name + ".bsp")
    hdr = bytearray(open(bsp, "rb").read())
    struct.pack_into("<I", hdr, 16 + 4, len(data))
    open(bsp, "wb").write(hdr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

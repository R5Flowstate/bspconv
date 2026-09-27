"""Converter input for BSP texdata that Titanfall 2 only ships as a legacy VMT.

    py vmt_materials.py <matl_cfg.json> <client .bsp> <vmt root> <texture root> <out dir>

TF2's engine still reads Source .vmt materials; S21 resolves every texdata name to a
material asset at load and reports "not found" for the rest. For each texdata name with
no TF2 rpak material this writes a TF2-form material json into <out dir> (add it to the
cfg's tf2_matl roots) and appends the S21 name to the wanted list:

  Basic (tool textures)  TF2's own tools/toolstrigger material under the new name. A
                         $basetexture TF2 never shipped leaves the albedo slot neutral.
  UnlitTwoTexture        authored on the S21 additive world set: $basetexture ->
                         emissive, $texture2 (first frame) -> opacity multiply,
                         $color2 -> emissive tint, albedo tint 0 (unlit).

Any other VMT shader is an error, never a guess.
"""
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tf2coll"))

VTF_DXT1, VTF_DXT5 = 13, 15
DXGI = {VTF_DXT1: 71, VTF_DXT5: 77}  # BC1_UNORM, BC3_UNORM
BLOCK = {VTF_DXT1: 8, VTF_DXT5: 16}
RESOURCE_HIGH_RES = b"\x30\x00\x00"

TOOL_TEMPLATE = "tools/toolstrigger"
# The one stock S21 world set that blends additively (echo_big_screen_animation_01_avt).
ADDITIVE_WORLD_SET = "0x49872912C6077316"
ADDITIVE_WORLD_TEMPLATE = "world/signs/beacon_screens/echo_big_screen_animation_01_avt_wldc"


def load_json(p):
    t = open(p, encoding="utf-8", errors="replace").read()
    t = re.sub(r"//[^\n]*", "", t)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", t))


def texdata_names(bsp):
    def lump(i):
        return open("%s.%04x.bsp_lump" % (bsp, i), "rb").read()
    strings = lump(0x0F)
    td = lump(0x02)
    out = []
    for i in range(0, len(td), 16):
        o = struct.unpack_from("<i", td, i)[0]
        out.append(strings[o:strings.index(b"\0", o)].decode("latin1"))
    return out


def norm(name):
    return name.replace("\\", "/").lower()


def parse_vmt(path):
    """Top-level shader name and its flat key/values (proxies ignored)."""
    text = re.sub(r"//[^\n]*", "", open(path, encoding="latin1").read())
    toks = re.findall(r'"[^"]*"|[{}]|[^\s{}"]+', text)
    shader = toks[0].strip('"')
    kv = {}
    depth = 0
    i = 1
    while i < len(toks):
        t = toks[i]
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
        elif depth == 1 and i + 1 < len(toks) and toks[i + 1] not in ("{", "}"):
            kv[t.strip('"').lower()] = toks[i + 1].strip('"')
            i += 1
        i += 1
    return shader, kv


def vtf_first_frame(path):
    d = open(path, "rb").read()
    if d[:4] != b"VTF\0" or struct.unpack_from("<II", d, 4) != (7, 5):
        raise ValueError("%s: need VTF 7.5" % path)
    w, h, _flags, frames = struct.unpack_from("<HHIH", d, 16)
    fmt, mips = struct.unpack_from("<iB", d, 52)
    if fmt not in DXGI:
        raise ValueError("%s: unsupported VTF format %d" % (path, fmt))
    count = struct.unpack_from("<I", d, 0x44)[0]
    ofs = None
    for i in range(count):
        tag, _f, o = struct.unpack_from("<3sBI", d, 0x50 + 8 * i)
        if tag == RESOURCE_HIGH_RES:
            ofs = o
    if ofs is None:
        raise ValueError("%s: no high-res image" % path)

    def size(m):
        return max(1, ((w >> m) + 3) // 4) * max(1, ((h >> m) + 3) // 4) * BLOCK[fmt]
    # VTF order: mip smallest first, then frame.
    mipdata = {}
    p = ofs
    for m in reversed(range(mips)):
        mipdata[m] = d[p:p + size(m)]
        p += size(m) * frames
    return w, h, mips, fmt, [mipdata[m] for m in range(mips)]


def write_dds(path, w, h, mips, fmt, data):
    hdr = bytearray(4 + 124 + 20)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, 0xA1007, h, w, len(data[0]), 1, mips)
    struct.pack_into("<2I4s", hdr, 4 + 72, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 4 + 104, 0x401008)
    struct.pack_into("<5I", hdr, 128, DXGI[fmt], 3, 0, 1, 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(bytes(hdr) + b"".join(data))
    json.dump({"streamLayout": ["permanent"] * (mips - 1)}, open(path[:-4] + ".json", "w"), indent=1)


def convert_texture(vmt_root, tex_root, name):
    """VTF under the VMT root -> texture/<name>.dds; None when TF2 never shipped it."""
    src = os.path.join(vmt_root, norm(name) + ".vtf")
    if not os.path.isfile(src):
        return None
    w, h, mips, fmt, data = vtf_first_frame(src)
    write_dds(os.path.join(tex_root, "texture", norm(name) + ".dds"), w, h, mips, fmt, data)
    return "texture/%s.rpak" % norm(name)


def find_tf2(roots, name, typ):
    for r in roots:
        p = os.path.join(r, "material", "%s_%s.json" % (name, typ))
        if os.path.isfile(p):
            return p
    return None


def color(v):
    vals = [float(x) for x in re.findall(r"-?[\d.]+", v)]
    if len(vals) != 3:
        raise ValueError("bad color %r" % v)
    return vals


def tool_material(cfg, name, kv, vmt_root, tex_root):
    base = find_tf2(cfg["tf2_matl"], TOOL_TEMPLATE, "wld")
    if not base:
        raise ValueError("TF2 %s_wld not in the TF2 material roots" % TOOL_TEMPLATE)
    m = load_json(base)
    m["name"] = name
    tex = convert_texture(vmt_root, tex_root, kv["$basetexture"]) if "$basetexture" in kv else None
    m["$textures"] = {"0": tex} if tex else {}
    m["$textureTypes"] = {"0": "albedoTexture"} if tex else {}
    return m, open(base[:-5] + ".uber", "rb").read()


def unlit_two_texture(cfg, name, kv, vmt_root, tex_root):
    if kv.get("$additive") != "1":
        raise ValueError("%s: only additive UnlitTwoTexture is mapped" % name)
    tmpl = None
    for r in cfg["s21_matl"]:
        for root, _, files in os.walk(r):
            if os.path.basename(ADDITIVE_WORLD_TEMPLATE) + ".json" in files:
                tmpl = os.path.join(root, os.path.basename(ADDITIVE_WORLD_TEMPLATE) + ".json")
                break
        if tmpl:
            break
    if not tmpl:
        raise ValueError("stock %s not in the S21 material roots" % ADDITIVE_WORLD_TEMPLATE)
    m = load_json(tmpl)
    textures, types = {}, {}
    for slot, (key, bind) in enumerate((("$basetexture", "emissiveTexture"), ("$texture2", "opacityMultiplyTexture"))):
        if key in kv:
            t = convert_texture(vmt_root, tex_root, kv[key])
            if not t:
                raise ValueError("%s: %s %s has no VTF" % (name, key, kv[key]))
            textures[str(slot)] = t
            types[str(slot)] = bind
    m.update({
        "name": name,
        "shaderType": "wld",
        "$textures": textures,
        "$textureTypes": types,
        "$s21ShaderSet": ADDITIVE_WORLD_SET,
        "$why": "TF2 draws it with the legacy UnlitTwoTexture VMT shader; S21's only additive world set",
        "$uberOverrides": {
            "c_zUpBlendingVertexAlphaAndL0_albedoTint": [None, 0.0, 0.0, 0.0],
            "c_depthBlendScalarAndL0_emissiveTint": [None] + color(kv.get("$color2", "[1 1 1]")),
        },
    })
    for k in ("$depthShadowMaterial", "$depthPrepassMaterial", "$depthVSMMaterial",
              "$depthShadowTightMaterial", "$colpassMaterial", "$textureAnimation"):
        m[k] = "0x0"
    return m, None


BUILDERS = {"basic": tool_material, "unlittwotexture": unlit_two_texture}


def main(argv):
    cfg_path, bsp, vmt_root, tex_root, out_dir = argv
    cfg = json.load(open(cfg_path))
    known = set()
    for r in cfg["tf2_matl"]:
        if os.path.abspath(r) == os.path.abspath(out_dir):
            continue
        for root, _, files in os.walk(os.path.join(r, "material")):
            for f in files:
                if f.endswith(".json"):
                    rel = os.path.relpath(os.path.join(root, f), os.path.join(r, "material"))
                    known.add(norm(rel[:-5]).rsplit("_", 1)[0])
    wanted = [l.strip() for l in open(cfg["wanted"]) if l.strip()]
    added = 0
    for raw in sorted(set(texdata_names(bsp))):
        name = norm(raw)
        if name in known:
            continue
        vmt = os.path.join(vmt_root, name + ".vmt")
        if not os.path.isfile(vmt):
            raise ValueError("texdata %s has no TF2 material and no VMT" % raw)
        shader, kv = parse_vmt(vmt)
        build = BUILDERS.get(shader.lower())
        if not build:
            raise ValueError("%s: VMT shader %s is not mapped" % (raw, shader))
        m, uber = build(cfg, name, kv, vmt_root, tex_root)
        p = os.path.join(out_dir, "material", name + "_wld")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(m, open(p + ".json", "w"), indent=1)
        if uber is not None:
            open(p + ".uber", "wb").write(uber)
        if name + "_wldc" not in wanted:
            wanted.append(name + "_wldc")
            added += 1
        print("  %-45s %s -> %s" % (raw, shader, sorted(m["$textures"].values()) or "neutral"))
    with open(cfg["wanted"], "w") as f:
        f.write("\n".join(wanted) + "\n")
    print("wanted +%d" % added)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

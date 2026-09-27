"""Convert a Titanfall 2 map's baked cubemaps into the S21 map cubemap texture.

    py cubemaps.py <tf2.bsp> <map name> <texture root>

TF2 ships them as one VTF in the pakfile lump (materials/maps/<tf2 map>/cubemaps.hdr.vtf):
one frame per cubemap, six faces each, BC6H. The S21 client loads
texture/maps/<map>/cubemaps_hdr.rpak and requires arraySize == 6 * cubemap count
(Mod_LoadCubemapArray); anything else falls back to the one-cube default and every
sample index past 0 reads outside the array. The blocks are the same encoding on
both sides, so this only reorders them: VTF stores mip-major (smallest first), frame,
face; the DDS array stores slice-major (frame*6 + face), largest mip first.

Writes <texture root>/texture/maps/<map>/cubemaps_hdr.dds + .json (layerCount 6 marks
the cube for the manifest).
"""
import io
import json
import os
import struct
import sys
import zipfile

VTF_FORMAT_BC6H_UF16 = 66
DXGI_FORMAT_BC6H_UF16 = 95
VTF_FLAG_ENVMAP = 0x4000
RESOURCE_HIGH_RES = b"\x30\x00\x00"


def lump_path(bsp, idx):
    return "%s.%04x.bsp_lump" % (bsp, idx)


def read_lump(bsp, idx):
    side = lump_path(bsp, idx)
    if os.path.isfile(side):
        return open(side, "rb").read()
    raw = open(bsp, "rb").read()
    ofs, size = struct.unpack_from("<II", raw, 16 + 16 * idx)
    return raw[ofs:ofs + size]


def find_vtf(pakfile):
    z = zipfile.ZipFile(io.BytesIO(pakfile))
    names = [n for n in z.namelist() if n.lower().endswith("/cubemaps.hdr.vtf")]
    if len(names) != 1:
        raise ValueError("expected one cubemaps.hdr.vtf in the pakfile lump, found %s" % names)
    return names[0], z.read(names[0])


def mip_bytes(w, h, m):
    bw = max(1, (max(1, w >> m) + 3) // 4)
    bh = max(1, (max(1, h >> m) + 3) // 4)
    return bw * bh * 16


def parse_vtf(d):
    if d[:4] != b"VTF\0":
        raise ValueError("not a VTF")
    major, minor, hdr_size = struct.unpack_from("<III", d, 4)
    w, h, flags, frames, _first = struct.unpack_from("<HHIHH", d, 16)
    fmt, mips, low_fmt = struct.unpack_from("<iBi", d, 52)
    depth = struct.unpack_from("<H", d, 63)[0] if minor >= 2 else 1
    if (major, minor) != (7, 5):
        raise ValueError("unsupported VTF %d.%d" % (major, minor))
    if fmt != VTF_FORMAT_BC6H_UF16:
        raise ValueError("unsupported VTF format %d" % fmt)
    if not flags & VTF_FLAG_ENVMAP or depth != 1:
        raise ValueError("not a 2D cube map (flags 0x%x depth %d)" % (flags, depth))
    count = struct.unpack_from("<I", d, 0x44)[0]
    data_ofs = None
    for i in range(count):
        tag, _flag, ofs = struct.unpack_from("<3sBI", d, 0x50 + 8 * i)
        if tag == RESOURCE_HIGH_RES:
            data_ofs = ofs
    if data_ofs is None:
        raise ValueError("VTF has no high-res image resource")
    faces = 6
    per_mip = [mip_bytes(w, h, m) for m in range(mips)]
    need = sum(per_mip) * frames * faces
    if data_ofs + need > len(d):
        raise ValueError("VTF image data truncated: need %d at %d, file %d" % (need, data_ofs, len(d)))
    # [frame][face][mip] -> bytes, walking the VTF order.
    blocks = [[[None] * mips for _ in range(faces)] for _ in range(frames)]
    p = data_ofs
    for m in reversed(range(mips)):
        for fr in range(frames):
            for fc in range(faces):
                blocks[fr][fc][m] = d[p:p + per_mip[m]]
                p += per_mip[m]
    return w, h, mips, frames, blocks


def dds_bytes(w, h, mips, frames, blocks):
    slices = frames * 6
    hdr = bytearray(4 + 124 + 20)
    hdr[0:4] = b"DDS "
    # caps: DDSD_CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT|LINEARSIZE
    struct.pack_into("<7I", hdr, 4, 124, 0xA1007, h, w, mip_bytes(w, h, 0), 1, mips)
    struct.pack_into("<2I4s", hdr, 4 + 72, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 4 + 104, 0x401008)  # COMPLEX|TEXTURE|MIPMAP
    # dxgi, TEXTURE2D, misc 0, arraySize = faces (the engine's own layout), alpha unknown
    struct.pack_into("<5I", hdr, 128, DXGI_FORMAT_BC6H_UF16, 3, 0, slices, 0)
    out = bytearray(hdr)
    for fr in range(frames):
        for fc in range(6):
            for m in range(mips):
                out += blocks[fr][fc][m]
    return bytes(out)


def main(argv):
    bsp, name, root = argv[0], argv[1], argv[2]
    vtf_name, vtf = find_vtf(read_lump(bsp, 0x28))
    w, h, mips, frames, blocks = parse_vtf(vtf)
    samples = len(read_lump(bsp, 0x2A)) // 16
    if frames != samples:
        raise ValueError("%s has %d cubemaps, the BSP declares %d" % (vtf_name, frames, samples))
    out_dir = os.path.join(root, "texture", "maps", name)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, "cubemaps_hdr")
    open(stem + ".dds", "wb").write(dds_bytes(w, h, mips, frames, blocks))
    json.dump({"streamLayout": ["permanent"] * (mips - 1), "layerCount": 6},
              open(stem + ".json", "w"), indent=1)
    print("%s: %d cubemaps %dx%d, %d mips -> %s.dds (%d slices)" % (vtf_name, frames, w, h, mips, stem, frames * 6))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

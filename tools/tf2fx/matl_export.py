"""A particle material (matl, ptcu/ptcs) read from a pak as a Repak material doc."""
import struct

import rpak

SHADER_TYPE = {
    8: "ptcu",
    9: "ptcs",
}


def name_of(pk, hdr, at=0x18):
    pi, po = struct.unpack_from("<ii", hdr, at)
    try:
        return pk.cstr(rpak.PagePtr(pi, po))
    except Exception:
        return ""


def surface_of(pk, hdr, at):
    pi, po = struct.unpack_from("<ii", hdr, at)
    if pi == 0 and po == 0:
        return ""
    try:
        return pk.cstr(rpak.PagePtr(pi, po))
    except Exception:
        return ""


def textures_of(pk, hdr):
    pi, po = struct.unpack_from("<ii", hdr, 0x60)
    if pi == 0 and po == 0:
        return []
    out = []
    for i in range(16):
        try:
            g, = struct.unpack_from("<Q", pk.read(rpak.PagePtr(pi, po + 8 * i), 8))
        except Exception:
            break
        if g == 0:
            break
        out.append(g)
    return out


def uber_of(pk, a):
    cpu = pk.read(a.data, 16)
    pi, po, sz, ver = struct.unpack_from("<iiII", cpu)
    return pk.read(rpak.PagePtr(pi, po), sz), ver


def s21_to_repak(pk, a):
    hdr = pk.read(a.head, 256)
    name = name_of(pk, hdr)
    surf = surface_of(pk, hdr, 0x20)
    surf2 = surface_of(pk, hdr, 0x28)
    passes = struct.unpack_from("<5Q", hdr, 0x30)
    shds, = struct.unpack_from("<Q", hdr, 0x58)
    nstream, width, height, depth = struct.unpack_from("<hhhh", hdr, 0x70)
    samplers = struct.unpack_from("<Q", hdr, 0x78)[0]
    features, unk84, flags, flags2 = struct.unpack_from("<IIII", hdr, 0x80)
    blends = struct.unpack_from("<8I", hdr, 0x90)
    mask, = struct.unpack_from("<I", hdr, 0xB0)
    depth_flags, rast = struct.unpack_from("<HH", hdr, 0xB4)
    dx_unk28, = struct.unpack_from("<Q", hdr, 0xB8)
    unk_c0, unk_c4 = struct.unpack_from("<ff", hdr, 0xC0)
    nanim, mtype, uber_flags = struct.unpack_from("<HBB", hdr, 0xC8)
    unk_cc, = struct.unpack_from("<I", hdr, 0xCC)
    texan, = struct.unpack_from("<Q", hdr, 0xD0)
    unk_e8, unk_ec = struct.unpack_from("<ff", hdr, 0xE8)
    unk_f0, = struct.unpack_from("<I", hdr, 0xF0)
    uber, uber_ver = uber_of(pk, a)
    tex = textures_of(pk, hdr)
    stype = SHADER_TYPE.get(mtype, "ptcu")
    doc = {
        "name": name,
        "width": int(width),
        "height": int(height),
        "depth": int(depth),
        "glueFlags": "0x%X" % flags,
        "glueFlags2": "0x%X" % flags2,
        "blendStates": ["0x%X" % b for b in blends],
        "blendStateMask": "0x%X" % mask,
        "depthStencilFlags": "0x%X" % depth_flags,
        "rasterizerFlags": "0x%X" % rast,
        "uberBufferFlags": "0x%X" % uber_flags,
        "features": "0x%X" % features,
        "samplers": "0x%X" % samplers,
        "surfaceProp": surf or "default",
        "surfaceProp2": surf2,
        "shaderType": stype,
        "shaderSet": "0x%016X" % shds,
        "$textures": {str(i): "0x%016X" % g for i, g in enumerate(tex)},
        "$textureTypes": {str(i): "albedoTexture" for i in range(len(tex))},
        "$depthShadowMaterial": "0x%016X" % passes[0],
        "$depthPrepassMaterial": "0x%016X" % passes[1],
        "$depthVSMMaterial": "0x%016X" % passes[2],
        "$depthShadowTightMaterial": "0x%016X" % passes[3],
        "$colpassMaterial": "0x%016X" % passes[4],
        "$textureAnimation": "0x%016X" % texan,
        "dxStateUnk28": "0x%X" % dx_unk28,
        "unk_C0": "0x%X" % struct.unpack("<I", struct.pack("<f", unk_c0))[0],
        "unk_CC": "0x%X" % unk_cc,
        "unk_E8": "0x%X" % struct.unpack("<I", struct.pack("<f", unk_e8))[0],
        "unk_EA": "0x0",
        "unk_84": "0x%X" % unk84,
        "unk_F0": "0x%X" % unk_f0,
    }
    return doc, uber, name, stype

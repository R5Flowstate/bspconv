"""Parse baked particle-effect (`efct`) assets out of an uncompressed rpak.

Disk layout:
  0x00 name ptr        0x08 opsCheckSum      0x10 childRefs ptr   0x18 scriptRefs ptr
  0x20 childRefCount   0x24 scriptRefCount   0x28 parms           parms_end operatorLists[6]
Each list is {ptr to an array of operator pointers, u64 count}; each operator is a 128-byte
ParticleOperator_Disk whose type-specific payload sits at op+typeSpecificParmsOffset.

The parms block size differs between builds, so the operator-list base is probed from the
pointer-relocation table.
"""
import struct

from rpak import PagePtr

LIST_NAMES = ["initializers", "operators", "renderers", "emitters", "forcegenerators", "constraints"]
OPERATOR_DISK_SIZE = 128
DEF_HEADER_SIZE = 0x28


def _lists_valid(pak, asset, base_off):
    pi, po = asset.data.index, asset.data.offset
    fo = pak.file_off(asset.data)
    total = 0
    for k in range(6):
        off = base_off + 0x10 * k
        (count,) = struct.unpack_from("<Q", pak.buf, fo + off + 8)
        is_rel = (pi, po + off) in pak.reloc
        if count == 0:
            if is_rel:
                return -1
            continue
        if count > 512 or not is_rel:
            return -1
        arr = pak.deref(PagePtr(pi, po + off))
        for j in range(count):
            slot = PagePtr(arr.index, arr.offset + 8 * j)
            if (slot.index, slot.offset) not in pak.reloc:
                return -1
            op = pak.deref(slot)
            ti, ts = struct.unpack_from("<HH", pak.buf, pak.file_off(op))
            if ti > 300 or ts > 4096:
                return -1
            total += 1
    return total


def probe_list_offset(pak, sample=300):
    """Find the operator-list base by the only offset that yields a wholly consistent
    6-list structure across many assets."""
    from collections import Counter
    votes = Counter()
    n = 0
    for a in pak.by_type("efct"):
        if not a.data.valid():
            continue
        if pak.page_file_off[a.data.index] is None:
            continue
        n += 1
        if n > sample:
            break
        for x in range(0x100, 0x300, 8):
            try:
                if _lists_valid(pak, a, x) > 0:
                    votes[x] += 1
            except Exception:
                pass
    if not votes:
        return None
    # A window shifted one list down also validates when the trailing list is empty and the
    # 16 bytes below the real base are a null string, so take the largest validating base.
    strong = [x for x, n in votes.items() if n > votes.most_common(1)[0][1] * 0.5]
    return max(strong)


def parse_operator(pak, op_ptr):
    fo = pak.file_off(op_ptr)
    (op_type, ts_size, vis_off, init_off, ts_off,
     asset_first, asset_count) = struct.unpack_from("<7H", pak.buf, fo)
    rec = {
        "opTypeIndex": op_type,
        "typeSpecificParmsSize": ts_size,
        "visibilityInputsOffset": vis_off,
        "initializerCommonParmsOffset": init_off,
        "typeSpecificParmsOffset": ts_off,
        "typeSpecificAssetFirstIndex": asset_first,
        "typeSpecificAssetCount": asset_count,
        "id": pak.buf[fo + 0x10:fo + 0x20].hex(),
        "commonParms": pak.buf[fo + 0x20:fo + 0x80].hex(),
    }
    if ts_size:
        rec["typeSpecificParms"] = pak.buf[fo + ts_off:fo + ts_off + ts_size].hex()
        # relocations inside the payload are GUID/pointer slots the converter must carry
        rec["payloadReloc"] = pak.reloc_range(op_ptr.index, op_ptr.offset + ts_off,
                                              op_ptr.offset + ts_off + ts_size)
    return rec


def parse_effect(pak, asset, list_off):
    fo = pak.file_off(asset.data)
    pi, po = asset.data.index, asset.data.offset
    (child_count, script_count) = struct.unpack_from("<II", pak.buf, fo + 0x20)
    rec = {
        "guid": "0x%016X" % asset.guid,
        "version": asset.version,
        "name": pak.cstr(pak.deref(PagePtr(pi, po))) if (pi, po) in pak.reloc else None,
        "opsCheckSum": "0x%016X" % struct.unpack_from("<Q", pak.buf, fo + 8)[0],
        "childRefCount": child_count,
        "scriptRefCount": script_count,
        "parms": pak.buf[fo + DEF_HEADER_SIZE:fo + list_off].hex(),
        "parmsReloc": pak.reloc_range(pi, po + DEF_HEADER_SIZE, po + list_off),
        "operatorLists": {},
    }
    hdr = pak.read(asset.head, asset.header_size)
    (crc_i, crc_o, arf_i, arf_o, n_child, n_asset) = struct.unpack_from("<6I", hdr, 0)
    rec["headerChildRefCount"] = n_child
    rec["headerAssetRefCount"] = n_asset
    for name, cnt, ptr in (("childEffects", n_child, PagePtr(crc_i, crc_o)),
                           ("assetRefs", n_asset, PagePtr(arf_i, arf_o))):
        guids = []
        if cnt:
            b = pak.file_off(ptr)
            guids = ["0x%016X" % g for (g,) in struct.iter_unpack("<Q", pak.buf[b:b + 8 * cnt])]
        rec[name] = guids
    if child_count:
        cp = pak.deref(PagePtr(pi, po + 0x10))
        b = pak.file_off(cp)
        rec["childRefs"] = [pak.buf[b + 40 * i:b + 40 * (i + 1)].hex() for i in range(child_count)]
    if script_count:
        sp = pak.deref(PagePtr(pi, po + 0x18))
        rec["scriptRefs"] = [pak.cstr(pak.deref(PagePtr(sp.index, sp.offset + 8 * i)))
                             for i in range(script_count)]
    for k, lname in enumerate(LIST_NAMES):
        off = list_off + 0x10 * k
        (count,) = struct.unpack_from("<Q", pak.buf, fo + off + 8)
        ops = []
        if count:
            arr = pak.deref(PagePtr(pi, po + off))
            for j in range(count):
                ops.append(parse_operator(pak, pak.deref(PagePtr(arr.index, arr.offset + 8 * j))))
        rec["operatorLists"][lname] = ops
    return rec


def parse_pak(path, limit=None):
    from rpak import Pak
    pak = Pak(path)
    list_off = probe_list_offset(pak)
    if list_off is None:
        return pak, None, []
    out = []
    for a in pak.by_type("efct"):
        if not a.data.valid():
            continue
        out.append(parse_effect(pak, a, list_off))
        if limit and len(out) >= limit:
            break
    return pak, list_off, out


if __name__ == "__main__":
    import json
    import sys
    pak, list_off, effects = parse_pak(sys.argv[1])
    print("%s: efct=%d parsed=%d operatorListOffset=0x%X"
          % (sys.argv[1], len(pak.by_type("efct")), len(effects), list_off or 0))
    if len(sys.argv) > 2:
        json.dump(effects, open(sys.argv[2], "w"))
        print("wrote", sys.argv[2])

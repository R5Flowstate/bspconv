"""Dump the S21 baked effects that share a name with a TF2 particle system.

    py s21_corpus.py <out.pkl> <names.json> <s21 dec paks...>

Per effect: parms bytes, the three inline strings, child names and child-info
bytes, asset GUIDs, and every operator's full disk bytes (header through the
end of its payload). This is the oracle the TF2 converter is calibrated and
verified against.
"""
import json
import os
import pickle
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
import efct  # noqa: E402
import rpak  # noqa: E402

LIST_NAMES = efct.LIST_NAMES


def op_bytes(pak, op_ptr):
    fo = pak.file_off(op_ptr)
    (ti, ts, vis, init, tso, af, ac) = struct.unpack_from("<7H", pak.buf, fo)
    end = max(96, tso + ts, vis, init)
    return bytes(pak.buf[fo:fo + end])


def main(out, names_path, paks):
    want = set(json.load(open(names_path)))
    guid_name = {}
    recs = {}
    for p in paks:
        pak = rpak.open_pak(p)
        lo = efct.probe_list_offset(pak)
        for a in pak.by_type("efct"):
            if not a.data.valid() or pak.page_file_off[a.data.index] is None:
                continue
            pi, po = a.data.index, a.data.offset
            if (pi, po) not in pak.reloc:
                continue
            name = pak.cstr(pak.deref(rpak.PagePtr(pi, po)))
            guid_name[a.guid] = name
            if name not in want or name in recs:
                continue
            fo = pak.file_off(a.data)
            parms = bytes(pak.buf[fo + 0x28:fo + lo])
            strings = {}
            for slot in (0x008, 0x048, 0x110):
                if (pi, po + 0x28 + slot) in pak.reloc:
                    strings[slot] = pak.cstr(pak.deref(rpak.PagePtr(pi, po + 0x28 + slot)))
            n_child = struct.unpack_from("<I", pak.buf, fo + 0x20)[0]
            child_info = b""
            if n_child and (pi, po + 0x10) in pak.reloc:
                child_info = pak.read(pak.deref(rpak.PagePtr(pi, po + 0x10)), 8 * n_child)
            hdr = pak.read(a.head, a.header_size)
            (crc_i, crc_o, arf_i, arf_o, hc, ha) = struct.unpack_from("<6I", hdr, 0)
            child_guids = list(struct.unpack("<%dQ" % hc, pak.read(rpak.PagePtr(crc_i, crc_o), 8 * hc))) if hc else []
            asset_guids = list(struct.unpack("<%dQ" % ha, pak.read(rpak.PagePtr(arf_i, arf_o), 8 * ha))) if ha else []
            lists = {}
            for k, lname in enumerate(LIST_NAMES):
                off = lo + 0x10 * k
                (count,) = struct.unpack_from("<Q", pak.buf, fo + off + 8)
                ops = []
                if count:
                    arr = pak.deref(rpak.PagePtr(pi, po + off))
                    for j in range(count):
                        ops.append(op_bytes(pak, pak.deref(rpak.PagePtr(arr.index, arr.offset + 8 * j))))
                lists[lname] = ops
            recs[name] = {"guid": a.guid, "parms": parms, "strings": strings, "childInfo": child_info,
                          "childGuids": child_guids, "assetGuids": asset_guids, "lists": lists}
        print(p, len(recs))
    for r in recs.values():
        r["childNames"] = [guid_name.get(g) for g in r["childGuids"]]
    pickle.dump({"effects": recs, "guidName": guid_name}, open(out, "wb"))
    print("effects", len(recs))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])

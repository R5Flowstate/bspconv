"""Per-operator S21 disk templates, measured over every stock S21 effect.

    py templates.py <out.json> <s21 dec paks...>

For each opTypeIndex: the dominant operator length, the header (vis/init/
payload offsets, payload size, asset count) and the per-byte mode of all
instances of that length. The converter starts every operator from its
template, so bytes TF2 never authored (S21-only fields, pads) carry the value
S21 itself ships most."""
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
import efct  # noqa: E402
import rpak  # noqa: E402


def main(out, paks):
    by_index = collections.defaultdict(list)
    seen = set()
    for p in paks:
        pak = rpak.open_pak(p)
        lo = efct.probe_list_offset(pak)
        for a in pak.by_type("efct"):
            if not a.data.valid() or pak.page_file_off[a.data.index] is None or a.guid in seen:
                continue
            seen.add(a.guid)
            fo = pak.file_off(a.data)
            pi, po = a.data.index, a.data.offset
            for k in range(6):
                off = lo + 0x10 * k
                (count,) = struct.unpack_from("<Q", pak.buf, fo + off + 8)
                if not count:
                    continue
                arr = pak.deref(rpak.PagePtr(pi, po + off))
                for j in range(count):
                    op = pak.deref(rpak.PagePtr(arr.index, arr.offset + 8 * j))
                    o = pak.file_off(op)
                    (ti, ts, vis, init, tso, af, ac) = struct.unpack_from("<7H", pak.buf, o)
                    end = max(96, tso + ts, vis, init)
                    by_index[ti].append((k, bytes(pak.buf[o:o + end])))
        print(p, len(seen))
    result = {}
    for ti, rows in sorted(by_index.items()):
        lengths = collections.Counter(len(b) for _, b in rows)
        length = lengths.most_common(1)[0][0]
        same = [b for _, b in rows if len(b) == length]
        mode = bytearray(length)
        for i in range(length):
            mode[i] = collections.Counter(b[i] for b in same).most_common(1)[0][0]
        mode[0x10:0x20] = b"\0" * 16
        lists = collections.Counter(k for k, _ in rows)
        (_, ts, vis, init, tso, af, ac) = struct.unpack_from("<7H", mode, 0)
        result[ti] = {"length": length, "instances": len(rows), "lengths": dict(lengths),
                      "list": efct.LIST_NAMES[lists.most_common(1)[0][0]],
                      "payloadSize": ts, "visOffset": vis, "initOffset": init, "payloadOffset": tso,
                      "assetCount": ac, "bytes": bytes(mode).hex()}
    json.dump(result, open(out, "w"), indent=1)
    print("operator templates", len(result))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])

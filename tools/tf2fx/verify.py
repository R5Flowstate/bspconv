"""Prove convert_pcf against S21's own bakes of the same TF2 systems.

    py verify.py <pcf dir> <s21_corpus.pkl> [--detail OPNAME]

Converts every shared effect whose operator sequence matches S21's and diffs
the result byte by byte: operators per class (the UniqueId is excluded; S21
regenerated them), the parms block, child info and asset refs. Mismatching
byte offsets are aggregated per class so a wrong field shows as one row.
"""
import argparse
import collections
import struct

import convert_pcf
import corpus
import dmx


def parse_blob(blob, pointers):
    ptr = {at: to for at, to in pointers}
    parms = blob[0x28:0x28 + convert_pcf.S21_PARMS_SIZE]
    lists = {}
    lists_at = 0x28 + convert_pcf.S21_PARMS_SIZE
    for k, ln in enumerate(corpus.LIST_NAMES):
        count = struct.unpack_from("<Q", blob, lists_at + 0x10 * k + 8)[0]
        ops = []
        if count:
            arr = ptr[lists_at + 0x10 * k]
            for i in range(count):
                at = ptr[arr + 8 * i]
                (ti, ts, vis, init, tso) = struct.unpack_from("<5H", blob, at)
                ops.append(blob[at:at + max(96, tso + ts, vis, init)])
        lists[ln] = ops
    n_child = struct.unpack_from("<I", blob, 0x20)[0]
    info = blob[ptr[0x10]:ptr[0x10] + 8 * n_child] if n_child else b""
    return parms, lists, info


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pcf_dir")
    ap.add_argument("s21_pkl")
    ap.add_argument("--detail", default=None)
    args = ap.parse_args(argv)
    conv = convert_pcf.Converter(args.pcf_dir)
    s21 = corpus.load_s21(args.s21_pkl)["effects"]
    table = conv.table
    op_stats = collections.defaultdict(lambda: [0, 0])          # fn -> [exact, total]
    op_bytes = collections.defaultdict(collections.Counter)      # fn -> Counter(offset)
    parms_bytes = collections.Counter()
    totals = collections.Counter()
    for name, rec in s21.items():
        ok = True
        for ln in corpus.LIST_NAMES:
            t = [dmx.attr(o, "functionName").lower() for o in conv.tf.ops(name, ln)]
            s = [table[struct.unpack_from("<H", b, 0)[0]] for b in rec["lists"][ln]]
            if t != s:
                ok = False
                break
        if not ok:
            continue
        out = conv.convert_system(name)
        parms, lists, info = parse_blob(out["blob"], out["pointers"])
        totals["effects"] += 1
        for i in range(len(parms)):
            if i in (0x008, 0x048, 0x110) or 0x008 <= i < 0x028 or 0x048 <= i < 0x068 or 0x110 <= i < 0x130:
                continue
            if parms[i] != rec["parms"][i]:
                parms_bytes[i] += 1
        totals["parmsExact"] += all(parms[i] == rec["parms"][i] for i in range(len(parms))
                                    if not (0x008 <= i < 0x028 or 0x048 <= i < 0x068 or 0x110 <= i < 0x130))
        totals["infoExact"] += info == rec["childInfo"]
        mine = struct.unpack("<%dQ" % (len(out["assetGuids"]) // 8), out["assetGuids"])
        totals["assetsExact"] += list(mine) == list(rec["assetGuids"])
        for ln in corpus.LIST_NAMES:
            for a, b in zip(lists[ln], rec["lists"][ln]):
                fn = table[struct.unpack_from("<H", b, 0)[0]]
                st = op_stats[fn]
                st[1] += 1
                if len(a) != len(b):
                    op_bytes[fn]["len%d!=%d" % (len(a), len(b))] += 1
                    continue
                diff = [i for i in range(len(a)) if not (0x10 <= i < 0x20) and a[i] != b[i]]
                if not diff:
                    st[0] += 1
                for i in diff:
                    op_bytes[fn][i] += 1
    print("effects compared", totals["effects"], "parms exact", totals["parmsExact"],
          "child info exact", totals["infoExact"], "asset refs exact", totals["assetsExact"])
    print("parms mismatching offsets:", sorted(parms_bytes.items(), key=lambda x: -x[1])[:20])
    rows = sorted(op_stats.items(), key=lambda x: x[1][0] / max(1, x[1][1]))
    for fn, (ex, tot) in rows:
        worst = op_bytes[fn].most_common(8)
        print("%-55s %5d/%-5d %5.1f%%  %s" % (fn, ex, tot, 100.0 * ex / max(1, tot), worst))
    if args.detail:
        print(sorted(op_bytes[args.detail].items()))


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])

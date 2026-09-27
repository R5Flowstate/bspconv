"""Measure where each unpack field of each operator class lands in the S21 disk
operator, by joining TF2 .pcf operators to S21 baked operators of the same
effect (same list, same position, same function name).

    py calibrate.py <pcf dir> <s21_corpus.pkl> <out.json>

For every field, the value TF2 authored (or the unpack default when the .pcf
omits it) is encoded to bytes; an instance votes for disk offset p only when
those bytes occur exactly once in the S21 operator. The report keeps every
field's vote table so the chosen offset carries its evidence.
"""
import collections
import json
import os
import struct
import sys

import corpus
import dmx
import unpack

HERE = os.path.dirname(os.path.abspath(__file__))


def main(pcf_dir, s21_pkl, out):
    tf = corpus.Tf2Corpus(pcf_dir)
    s21 = corpus.load_s21(s21_pkl)["effects"]
    tbl = corpus.load_s21_table()
    classes = unpack.load()
    votes = collections.defaultdict(collections.Counter)     # (op, field) -> Counter(disk offset)
    seen = collections.Counter()                              # (op, field) -> instances
    sizes = collections.defaultdict(collections.Counter)      # op -> Counter(disk op length)
    pairs = 0
    for name, rec in s21.items():
        for ln in corpus.LIST_NAMES:
            tops = tf.ops(name, ln)
            sops = rec["lists"][ln]
            if len(tops) != len(sops):
                break
            names_t = [dmx.attr(o, "functionName").lower() for o in tops]
            names_s = [tbl[struct.unpack_from("<H", b, 0)[0]].lower() for b in sops]
            if names_t != names_s:
                break
        else:
            for ln in corpus.LIST_NAMES:
                for top, sop in zip(tf.ops(name, ln), rec["lists"][ln]):
                    fn = dmx.attr(top, "functionName")
                    cls = classes.by_name(fn)
                    if cls is None:
                        continue
                    pairs += 1
                    sizes[fn][len(sop)] += 1
                    for field in cls["fields"]:
                        enc = unpack.encode(field, top)
                        if enc is None:
                            continue
                        seen[(fn, field["name"])] += 1
                        hits = []
                        start = sop.find(enc)
                        while start != -1 and len(hits) < 2:
                            hits.append(start)
                            start = sop.find(enc, start + 1)
                        if len(hits) == 1:
                            votes[(fn, field["name"])][hits[0]] += 1
    report = {"pairs": pairs, "ops": {}}
    for fn in sorted({k[0] for k in seen}):
        cls = classes.by_name(fn)
        rows = []
        for field in cls["fields"]:
            key = (fn, field["name"])
            if key not in seen:
                continue
            v = votes[key]
            best = v.most_common(3)
            rows.append({"field": field["name"], "objOffset": field["offset"], "type": field["type"],
                         "instances": seen[key], "votes": [[o, c] for o, c in best]})
        report["ops"][fn] = {"class": cls["class"], "diskSizes": dict(sizes[fn]), "fields": rows}
    json.dump(report, open(out, "w"), indent=1)
    print("paired operators", pairs, "classes", len(report["ops"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])

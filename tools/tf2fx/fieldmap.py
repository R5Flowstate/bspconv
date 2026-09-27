"""Turn calibrate.py's vote tables into the field map the converter uses.

    py fieldmap.py <calib_ops.json> <s21_op_templates.json> <out field_map.json>

Per operator class:
  * payloadDelta: object offset minus disk offset for type-specific fields,
    the mode over every field whose vote is decisive
  * fields: name -> disk offset. A decisive vote wins (it also catches a
    field S21 moved); otherwise the block rule (common / init / visibility)
    or the class payload delta places it.
A vote is decisive when its top offset has at least 3 votes and at least
90% of that field's votes.
"""
import collections
import json
import sys

import corpus
import unpack

COMMON_OBJ = (0x08, 0x48)
COMMON_DISK = 0x20
BLOCK_OBJ = 0x90


def decisive(votes):
    if not votes:
        return None
    total = sum(c for _, c in votes)
    off, c = votes[0]
    return off if c >= 3 and c >= 0.9 * total else None


def main(calib_path, templates_path, out):
    calib = json.load(open(calib_path))["ops"]
    templates = {int(k): v for k, v in json.load(open(templates_path)).items()}
    table = [n.lower() for n in corpus.load_s21_table()]
    index = {n: i for i, n in enumerate(table)}
    result = {}
    for fn, rec in calib.items():
        idx = index.get(fn.lower())
        if idx is None or idx not in templates:
            continue
        t = templates[idx]
        vis, init, pay = t["visOffset"], t["initOffset"], t["payloadOffset"]
        deltas = collections.Counter()
        block_end = BLOCK_OBJ
        for f in rec["fields"]:
            d = decisive(f["votes"])
            if d is None:
                continue
            if d >= pay:
                deltas[f["objOffset"] - d] += 1
        payload_delta = deltas.most_common(1)[0][0] if deltas else None
        fields = {}
        voted = {f["field"]: f["votes"] for f in rec["fields"]}
        for uf in unpack.load().by_name(fn)["fields"]:
            f = {"field": uf["name"], "objOffset": uf["offset"], "votes": voted.get(uf["name"], [])}
            obj = f["objOffset"]
            d = decisive(f["votes"])
            if d is not None:
                fields[f["field"]] = d
                continue
            if COMMON_OBJ[0] <= obj < COMMON_OBJ[1]:
                fields[f["field"]] = COMMON_DISK + obj - COMMON_OBJ[0]
            elif vis and BLOCK_OBJ <= obj < BLOCK_OBJ + 0x40:
                fields[f["field"]] = vis + obj - BLOCK_OBJ
            elif init and BLOCK_OBJ <= obj < BLOCK_OBJ + 0x08:
                fields[f["field"]] = init + obj - BLOCK_OBJ
            elif payload_delta is not None and obj - payload_delta >= pay:
                fields[f["field"]] = obj - payload_delta
        result[fn.lower()] = {"index": idx, "payloadDelta": payload_delta, "fields": fields,
                              "deltaVotes": dict(deltas)}
    import os
    over_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field_overrides.json")
    for fn, o in json.load(open(over_path)).items():
        for field, disk in o["fields"].items():
            if disk is None:
                result[fn]["fields"].pop(field, None)
            else:
                result[fn]["fields"][field] = disk
    json.dump(result, open(out, "w"), indent=1)
    print("classes", len(result))


if __name__ == "__main__":
    main(*sys.argv[1:4])

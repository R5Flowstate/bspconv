"""Titanfall 2 particle systems (.pcf, DMX) -> S21 baked effects (efct v16).

    py convert_pcf.py <pcf dir> <out dir> <root effect names...> [--s21-names FILE]

Every system in the closure of the roots is written as an efct_def container
(the format tools/convert.py writes and Repak's AddEffectAsset_v16 reads).
A system whose name S21 already ships is not converted: its children point
at the stock GUID, because a second publish under the same name would replace
the stock effect for every map.

Field placement comes from the engine's own unpack tables (unpack.py): each
TF2 attribute lands at its object offset mapped to the S21 disk operator:
  common block   object 0x08..0x48 -> disk 0x20..0x60
  initializer    object 0x90       -> disk initOffset (16-byte block)
  visibility     object 0x90..0xD0 -> disk visOffset (renderers)
  payload        object P..        -> disk payloadOffset, P = 0x90 (+0x08 init) (+0x48 visibility)
System fields go through their packed parms offset (object - 0xC8) and the
parms map (data/parms_map.json, keyed by that offset). Bytes TF2 never authors start from the modal S21
operator (templates.py) and the modal S21 parms.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))

import corpus  # noqa: E402
import dmx  # noqa: E402
import unpack  # noqa: E402
from guid import string_to_guid  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

S21_OPS_CHECKSUM = 0xAB73DF0F161BD96D
S21_PARMS_SIZE = 312
LIST_NAMES = corpus.LIST_NAMES
SYSTEM_OBJ_BASE = 0xC8
COMMON_OBJ = (0x08, 0x48)
COMMON_DISK = 0x20
BLOCK_OBJ = 0x90
VIS_OBJ_SIZE = 0x48
INIT_OBJ_SIZE = 0x08
CUTLSTRING = 32
STR_MATERIAL, STR_FALLBACK, STR_AMBIENT = 0x008, 0x048, 0x110
PTCS_RENDERERS = {"render_rope", "render_sprite_trail"}
MODEL_RENDERER = "render models"


class Blob:
    def __init__(self):
        self.buf = bytearray()
        self.pointers = []

    def alloc(self, size, align=8):
        self.buf += b"\0" * ((-len(self.buf)) % align)
        at = len(self.buf)
        self.buf += b"\0" * size
        return at

    def write(self, at, data):
        self.buf[at:at + len(data)] = data

    def put_string(self, s):
        at = self.alloc(len(s) + 1, 1)
        self.buf[at:at + len(s)] = s
        return at

    def set_ptr(self, at, to):
        self.pointers.append((at, to))


def effect_guid(name):
    return string_to_guid("effect/%s.rpak" % name)


def material_path(tf2_material):
    """TF2 VMT path as S21 bakes it: forward slashes, extension kept."""
    return tf2_material.replace("\\", "/")


def material_guid(path, shader):
    base = path
    for ext in (".vmt", ".rson", ".rpak"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break
    return string_to_guid("material/%s_%s.rpak" % (base, shader))


def model_path(tf2_model):
    p = tf2_model.replace("\\", "/")
    if p.lower().startswith("models/"):
        p = p[len("models/"):]
    if p.lower().endswith(".mdl"):
        p = p[:-4]
    return "mdl/%s.rmdl" % p


class Converter:
    def __init__(self, pcf_dir, s21_names=None, vmt_root=None, known_guids=None):
        self.tf = corpus.Tf2Corpus(pcf_dir)
        self.classes = unpack.load()
        self.table = [n.lower() for n in corpus.load_s21_table()]
        self.index = {n: i for i, n in enumerate(self.table)}
        self.templates = {int(k): v for k, v in json.load(open(os.path.join(HERE, "s21_op_templates.json"))).items()}
        self.field_map = json.load(open(os.path.join(HERE, "field_map.json")))
        self.parms_map = {int(f["packed"], 0): (int(f["s21"], 0), f["size"])
                          for f in json.load(open(os.path.join(corpus.DATA, "parms_map.json")))}
        self.parms_default = open(os.path.join(corpus.DATA, "parms_default.bin"), "rb").read()
        self.invariants = {int(k, 16): v for k, v in
                           json.load(open(os.path.join(corpus.DATA, "parms_invariants.json")))["invariants"].items()}
        self.s21_names = s21_names or {}
        self.vmt_root = vmt_root
        self.known_guids = known_guids
        self.report = {"effects": 0, "stockChildren": {}, "droppedFields": {}, "droppedOps": {},
                       "materials": {}, "models": {}, "missingMaterials": {}}

    # ------------------------------------------------------------ operators
    def disk_offset(self, field, tmpl, fn):
        mapped = self.field_map.get(fn.lower())
        if mapped is not None:
            return mapped["fields"].get(field["name"])
        obj = field["offset"]
        if COMMON_OBJ[0] <= obj < COMMON_OBJ[1]:
            return COMMON_DISK + (obj - COMMON_OBJ[0])
        vis, init, pay = tmpl["visOffset"], tmpl["initOffset"], tmpl["payloadOffset"]
        base = BLOCK_OBJ
        if vis:
            if obj < BLOCK_OBJ + VIS_OBJ_SIZE:
                return vis + (obj - BLOCK_OBJ)
            base += VIS_OBJ_SIZE
        if init:
            if obj < BLOCK_OBJ + INIT_OBJ_SIZE:
                return init + (obj - BLOCK_OBJ)
            base += INIT_OBJ_SIZE
        if obj < base:
            return None
        return pay + (obj - base)

    def encode_field(self, field, element):
        t = field["type"]
        v = unpack.value(field, element)
        if t == dmx.AT_STRING:
            size = field["size"]
            if v is None or size <= 0 or size >= 1 << 20:
                return None     # CUtlString members (comments) have no disk slot
            b = str(v).encode("latin-1")
            if field["name"] == "sequence 0 model" and b:
                b = model_path(str(v)).encode("latin-1")
            if "collision group" in field["name"]:
                b = b.lower()   # S21 bakes collision group names lowercase
            return b[:size - 1] + b"\0" * (size - min(len(b), size - 1))
        if t == dmx.AT_VECTOR2 + dmx.ARRAY_BASE and field["arrayLen"]:
            pts = []
            if element is not None and field["name"] in element["attrs"]:
                pts = list(element["attrs"][field["name"]][1])
            elif v is not None and isinstance(v, str):
                vals = [float(x) for x in v.split()]
                pts = [tuple(vals[i:i + 2]) for i in range(0, len(vals) - 1, 2)]
            elif field.get("default"):
                vals = [float(x) for x in field["default"].split()]
                pts = [tuple(vals[i:i + 2]) for i in range(0, len(vals) - 1, 2)]
            n = field["arrayLen"]
            pts = pts[:n] + [(0.0, 0.0)] * (n - min(len(pts), n))
            return b"".join(struct.pack("<2f", *p) for p in pts)
        return unpack.encode(field, element)

    def convert_operator(self, op, name, report_key):
        fn = dmx.attr(op, "functionName") or ""
        idx = self.index.get(fn.lower())
        cls = self.classes.by_name(fn)
        if idx is None or cls is None or idx not in self.templates:
            self.report["droppedOps"][fn] = self.report["droppedOps"].get(fn, 0) + 1
            return None, None
        tmpl = self.templates[idx]
        out = bytearray(bytes.fromhex(tmpl["bytes"]))
        out[0x10:0x20] = op["guid"]
        model = None
        for field in cls["fields"]:
            off = self.disk_offset(field, tmpl, fn)
            data = self.encode_field(field, op)
            if off is None or data is None:
                continue
            if off + len(data) > len(out):
                if field["name"] in op["attrs"]:
                    k = "%s/%s" % (fn, field["name"])
                    self.report["droppedFields"][k] = self.report["droppedFields"].get(k, 0) + 1
                continue
            out[off:off + len(data)] = data
            if field["name"] == "sequence 0 model":
                model = data.split(b"\0", 1)[0].decode("latin-1")
        return bytes(out), (idx, fn, model)

    # --------------------------------------------------------------- system
    def convert_system(self, name):
        els, sd = self.tf.system(name)
        blob = Blob()
        base = blob.alloc(0x28 + S21_PARMS_SIZE + 6 * 16)
        struct.pack_into("<Q", blob.buf, base + 8, S21_OPS_CHECKSUM)
        parms_at = base + 0x28
        blob.write(parms_at, self.parms_default)
        for field in self.classes.system:
            if field["type"] == dmx.AT_STRING:
                continue
            packed = field["offset"] - SYSTEM_OBJ_BASE
            dst = self.parms_map.get(packed)
            data = unpack.encode(field, sd)
            if dst is None or data is None:
                continue
            blob.write(parms_at + dst[0], data[:dst[1]])
        for off, val in self.invariants.items():
            blob.buf[parms_at + off] = val

        material = dmx.attr(sd, "material")
        material = material_path(material) if material else "effects/white"
        fallback = dmx.attr(sd, "fallback replacement definition") or ""
        for slot, text in ((STR_MATERIAL, material), (STR_FALLBACK, fallback), (STR_AMBIENT, "")):
            b = text.encode("latin-1")
            blob.write(parms_at + slot, struct.pack("<QQQQ", 0, len(b) + 1, 1, len(b) + 1))
            blob.set_ptr(parms_at + slot, blob.put_string(b))
        blob.set_ptr(base, blob.put_string(name.encode("latin-1")))

        # operators, in disk list order
        lists_at = base + 0x28 + S21_PARMS_SIZE
        op_assets = []
        functions = set()
        for k, ln in enumerate(LIST_NAMES):
            built = []
            for op in self.tf.ops(name, ln):
                data, info = self.convert_operator(op, name, ln)
                if data is None:
                    continue
                idx, fn, model = info
                functions.add(fn.lower())
                # S21 records the running asset index on every operator, with or without assets.
                data = bytearray(data)
                if fn.lower() == MODEL_RENDERER and model:
                    struct.pack_into("<HH", data, 10, len(op_assets), 1)
                    op_assets.append(string_to_guid(model))
                    self.report["models"][model] = self.report["models"].get(model, 0) + 1
                else:
                    struct.pack_into("<HH", data, 10, len(op_assets), 0)
                built.append(bytes(data))
            if not built:
                continue
            arr = blob.alloc(8 * len(built))
            for i, data in enumerate(built):
                at = blob.alloc(len(data))
                blob.write(at, data)
                blob.set_ptr(arr + 8 * i, at)
            struct.pack_into("<Q", blob.buf, lists_at + 0x10 * k + 8, len(built))
            blob.set_ptr(lists_at + 0x10 * k, arr)

        shader = "ptcs" if functions & PTCS_RENDERERS and not self.is_refract(material) else "ptcu"
        mat_guid = material_guid(material, shader)
        self.report["materials"]["%s_%s" % (material, shader)] = "0x%016X" % mat_guid
        asset_guids = list(op_assets)
        if self.known_guids is None or mat_guid in self.known_guids:
            asset_guids.append(mat_guid)
        else:
            # Nothing ships this material (TF2 included); a raw guid would stay in the
            # slot, so only the name is kept and the engine takes its missing-material path.
            self.report["missingMaterials"]["%s_%s" % (material, shader)] = name

        # children
        child_guids, child_info, child_names = [], [], []
        for ch, target in self.tf.children(name):
            if target is None:
                continue
            cname = target["name"]
            child_names.append(cname)
            child_guids.append(self.s21_names.get(cname) or effect_guid(cname))
            delay = float(dmx.attr(ch, "delay", 0.0) or 0.0)
            end_cap = 1 if dmx.attr(ch, "end cap effect", False) else 0
            mute = 1 if dmx.attr(ch, "mute", False) else 0
            child_info.append(struct.pack("<fBBxx", delay, end_cap, mute))
        if child_guids:
            at = blob.alloc(8 * len(child_info))
            blob.write(at, b"".join(child_info))
            struct.pack_into("<I", blob.buf, base + 0x20, len(child_info))
            blob.set_ptr(base + 0x10, at)

        return {"guid": effect_guid(name), "name": name, "blob": bytes(blob.buf), "pointers": blob.pointers,
                "childGuids": struct.pack("<%dQ" % len(child_guids), *child_guids),
                "assetGuids": struct.pack("<%dQ" % len(asset_guids), *asset_guids),
                "children": child_names}

    def is_refract(self, material):
        """Refract materials bind ptcu even under trail renderers (every stock S21 case)."""
        if not self.vmt_root:
            return False
        import vmt
        p = os.path.join(self.vmt_root, "materials", material.replace("/", os.sep))
        if not os.path.isfile(p):
            return False
        return vmt.load(p)[0] == "refract"

    def closure(self, roots):
        seen, order, stack = set(), [], list(roots)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n not in self.tf.systems:
                raise KeyError("particle system %r is in no .pcf" % n)
            if n in self.s21_names:
                self.report["stockChildren"][n] = "0x%016X" % self.s21_names[n]
                continue
            order.append(n)
            for _, target in self.tf.children(n):
                if target is not None:
                    stack.append(target["name"])
        return order


def write_container(out_dir, rec):
    sub = os.path.join(out_dir, "effect")
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, "0x%016X" % rec["guid"])
    with open(path + ".efct_def", "wb") as f:
        f.write(b"EFCT")
        f.write(struct.pack("<3I", 1, len(rec["blob"]), len(rec["pointers"])))
        f.write(rec["blob"])
        for at, to in rec["pointers"]:
            f.write(struct.pack("<2I", at, to))
    for suffix, data in ((".efct_childrefs", rec["childGuids"]), (".efct_assetrefs", rec["assetGuids"])):
        if data:
            open(path + suffix, "wb").write(data)
        elif os.path.exists(path + suffix):
            os.remove(path + suffix)


def load_s21_names(path):
    """name -> guid of every stock S21 effect."""
    d = json.load(open(path))
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            if isinstance(v, str) and k.startswith("0x"):
                out[v] = int(k, 16)
            elif isinstance(v, str) and v.startswith("0x"):
                out[k] = int(v, 16)
        return out
    return {}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pcf_dir")
    ap.add_argument("out_dir")
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--s21-names", default=None, help="json name->guid (or guid->name) of stock S21 effects")
    ap.add_argument("--vmt-root", default=None, help="dir holding materials/particle/*.vmt")
    ap.add_argument("--known", nargs="*", default=None,
                    help="json lists/dicts of guids that exist (S21 universe, converted particle mats)")
    args = ap.parse_args(argv)
    s21 = load_s21_names(args.s21_names) if args.s21_names else {}
    known = None
    if args.known is not None:
        known = set()
        for path in args.known:
            d = json.load(open(path))
            items = d if isinstance(d, list) else (list(d.keys()) + [v.get("guid") for v in d.values() if isinstance(v, dict)])
            for x in items:
                if isinstance(x, dict):
                    x = x.get("guid")
                if isinstance(x, int):
                    known.add(x)
                elif isinstance(x, str) and x.lower().startswith("0x"):
                    known.add(int(x, 16))
                elif isinstance(x, str):
                    try:
                        known.add(int(x, 16))
                    except ValueError:
                        pass
    conv = Converter(args.pcf_dir, s21, args.vmt_root, known)
    names = conv.closure(args.roots)
    manifest = []
    for n in names:
        rec = conv.convert_system(n)
        write_container(args.out_dir, rec)
        manifest.append({"_type": "efct", "_path": "effect/0x%016X" % rec["guid"],
                         "$guid": "0x%016X" % rec["guid"], "name": n})
        conv.report["effects"] += 1
    json.dump(manifest, open(os.path.join(args.out_dir, "_manifest.json"), "w"), indent=1)
    json.dump(conv.report, open(os.path.join(args.out_dir, "_report.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in conv.report.items() if k != "materials"}, indent=1))
    print("materials:", json.dumps(conv.report["materials"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

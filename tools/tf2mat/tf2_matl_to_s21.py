"""Rebuild Titanfall 2 materials as S21 matl v23 on their twin S21 shader sets.

    py tf2_matl_to_s21.py <config.json>

Each output material is a Repak material .json + .uber:
  - shader set: the S21 twin proven by StringToGuid of the translated name
  - uber: every CBufUberStatic member filled by name from the TF2 material
    (merged S21 vectors 'AAndL0_B' take A in .x and TF2 'c_B' in .yzw; 'L0_'
    is TF2's single layer); members TF2 never had take the majority value of
    stock S21 materials on the same set
  - state flags: TF2 values (same encoding); per-build constants from the S21 set
  - depth / colpass: TF2 code_private refs map to the S21 code_private twin,
    per-material ones are converted in the same run
"""
import collections
import csv
import json
import math
import os
import re
import struct
import subprocess
import sys


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tf2common"))
sys.path.insert(0, os.path.dirname(__file__))
from guid import string_to_guid  # noqa: E402
import paths  # noqa: E402
import dxbc_rdef  # noqa: E402
import shaderset_twin  # noqa: E402

TYPE_MAP = {"wld": "wldc", "fix": "rgdp", "skn": "sknp", "rgd": "rgdp"}
# _rgdc/_sknc are the unpacked-position variants a model beyond Vector64 range binds.
S21_TO_TF2 = {"wldc": "wld", "rgdp": "fix", "rgdc": "fix", "sknp": "skn", "sknc": "skn"}
PASS_KEYS = ("$depthShadowMaterial", "$depthPrepassMaterial", "$depthVSMMaterial",
             "$depthShadowTightMaterial", "$colpassMaterial")
# Keys an authored TF2-form material (vmt_materials.py) carries for the converter only.
AUTHORED_KEYS = ("$s21ShaderSet", "$why", "$uberOverrides")
TEMPLATE_KEYS = ("features", "uberBufferFlags", "unk_CC", "unk_E8", "unk_EA", "unk_84", "unk_F0",
                 "unk_C0")


def pass_state(name):
    """matl+0xB8: the PSO render-pass id (u16) + topology (u16). TF2 stores a heap
    pointer there; stock sets it by pass: shadow/tightshadow 2, vsm 3, else 0."""
    if name.endswith(("_shadow", "_tightshadow")):
        return "0x2"
    if name.endswith("_vsm"):
        return "0x3"
    return "0x0"


def load_json_tree(root):
    out = []
    for r, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".json"):
                p = os.path.join(r, f)
                try:
                    j = json.load(open(p, encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                j["_file"] = p
                out.append(j)
    return out


def mat_guid(name, typ):
    return string_to_guid("material/%s_%s.rpak" % (name, typ))


def g(v):
    return int(v, 16) if isinstance(v, str) else int(v)


class Shaders:
    """guid -> msw path for TF2 and S21 shader / shader set exports."""

    def __init__(self, roots):
        self.path = {}
        for root in roots:
            for r, _, fs in os.walk(root):
                for f in fs:
                    if f.endswith(".msw"):
                        p = os.path.join(r, f)
                        stem = f[:-4]
                        kind = "shaderset" if "shaderset" in r.replace("\\", "/") else "shader"
                        if stem.startswith("0x"):
                            gid = int(stem, 16)
                        else:
                            gid = string_to_guid("%s/%s.rpak" % (kind, stem))
                        self.path.setdefault(gid, p)

    def add_children(self, adj_files):
        """Child shaders carry no bytecode; RSX's depfile names each one's parent."""
        def gid_of(tok):
            stem = tok.split("\\")[-1].split("/")[-1]
            if stem.startswith("0x"):
                return int(stem.split(".")[0], 16)
            return string_to_guid(tok.replace("\\", "/"))
        for f in adj_files:
            for line in open(f, encoding="utf-8", errors="replace"):
                parts = line.strip().split(",")
                if len(parts) == 2 and parts[0].startswith("shader\\") and parts[1].startswith("shader\\"):
                    child, parent = gid_of(parts[0]), gid_of(parts[1])
                    if child not in self.path and parent in self.path:
                        self.path[child] = self.path[parent]

    def set_ps(self, set_guid):
        p = self.path.get(set_guid)
        if not p:
            return None
        d = open(p, "rb").read()
        return struct.unpack_from("<Q", d, 5)[0]

    def rdef(self, shader_guid):
        if not hasattr(self, "_rdef"):
            self._rdef = {}
        if shader_guid not in self._rdef:
            p = self.path.get(shader_guid)
            self._rdef[shader_guid] = [r for r in (dxbc_rdef.rdef_of(b) for b in dxbc_rdef.blobs(open(p, "rb").read())) if r] if p else []
        return self._rdef[shader_guid]

    def uber_layout(self, shader_guid):
        for binds, cbs in self.rdef(shader_guid):
            for name, size, vars_ in cbs:
                if name == "CBufUberStatic":
                    return size, vars_
        return None

    def disasm(self, shader_guid):
        """Disassembly of the shader's first bytecode blob (cached by guid)."""
        if not hasattr(self, "_asm"):
            self._asm = {}
        if shader_guid not in self._asm:
            text = ""
            p = self.path.get(shader_guid)
            blobs = list(dxbc_rdef.blobs(open(p, "rb").read())) if p else []
            if blobs:
                cache = os.path.join(os.environ.get("TEMP", "."), "tf2mat_asm")
                os.makedirs(cache, exist_ok=True)
                f = os.path.join(cache, "%016X.dxbc" % shader_guid)
                if not os.path.isfile(f[:-5] + ".asm"):
                    open(f, "wb").write(blobs[0])
                    subprocess.run([paths.get("shaderDisassembler"), "--disassemble-flugan", f], capture_output=True)
                if os.path.isfile(f[:-5] + ".asm"):
                    text = open(f[:-5] + ".asm", encoding="utf-8", errors="replace").read()
            self._asm[shader_guid] = text
        return self._asm[shader_guid]

    def uv_forms(self, shader_guid):
        """uv index -> 'rot' (S21 angle/scale/center form, scroll before scale) or
        'pre' (matrix applied to uv + translate), read from how the PS uses the members."""
        ul = self.uber_layout(shader_guid)
        if not ul:
            return {}
        by_ofs = {o: n for n, o, _ in ul[1]}
        asm = self.disasm(shader_guid)
        forms = {}
        for i, c in re.findall(r"^\s*sincos [^,]+, [^,]+, cb0\[(\d+)\]\.([xyzw])", asm, re.M):
            m = re.match(r"c_uv(\d)RotScaleX$", by_ofs.get(int(i) * 16 + "xyzw".index(c) * 4, ""))
            if m:
                forms[int(m.group(1))] = "rot"
        for i, c in re.findall(r"^\s*add r\d+\.\w+, v\d\.\w+, cb0\[(\d+)\]\.([xyzw])", asm, re.M):
            m = re.match(r"c_uv(\d)Translate$", by_ofs.get(int(i) * 16 + "xyzw".index(c) * 4, ""))
            if m:
                forms.setdefault(int(m.group(1)), "pre")
        return forms

    def material_slots(self, shader_guid):
        """Material texture slots the PS binds (engine-owned binds sit at t40 and up)."""
        slots = {}
        for binds, _ in self.rdef(shader_guid):
            for name, typ, point, count in binds:
                if typ == 2 and point < 40:
                    slots[str(point)] = name
        return slots


def tf2_value_for(s21_name, tf2_vars, tf2_uber, size):
    """Bytes for one S21 uber member from the TF2 buffer, or None."""
    tv = {n: (o, s) for n, o, s in tf2_vars}

    def get(n, want):
        if n in tv and tv[n][1] >= want:
            o, _ = tv[n]
            return tf2_uber[o:o + want]
        return None

    direct = get(s21_name, size)
    if direct is not None:
        return direct, "same"
    base = s21_name.replace("c_L0_", "c_")
    if base != s21_name:
        v = get(base, size)
        if v is not None:
            return v, "layer0"
        m = re.match(r"c_(\w+?)([XYZ])$", base[2:] and base)
        if m and m.group(2) in "XYZ":
            v = get("c_" + m.group(1), 12)
            if v is not None:
                k = "XYZ".index(m.group(2))
                return v[4 * k:4 * k + 4], "component"
    m = re.match(r"c_(\w+?)And(\w+)$", s21_name)
    if m and size == 16:
        def part(n, want):
            if n.startswith("L1_"):
                return None
            n = n.replace("L0_", "", 1)
            return get("c_" + RENAMED.get(n, n), want)
        a, b = m.group(1), m.group(2)
        # The vec3 half is the tint/colour; the other half is the scalar lane.
        if a.lower().endswith(("tint", "color")):
            xyz, w = part(a, 12), part(b, 4)
            if xyz is not None or w is not None:
                return (xyz, w), "merged_xyz_w"
        else:
            x, yzw = part(a, 4), part(b, 12)
            if x is not None or yzw is not None:
                return (x, yzw), "merged"
    return None


# common.rpak defaults for slots a set binds that no source material fills.
DEFAULT_TEXTURES = {
    "albedoTexture": "texture/defaults/default_col.rpak",
    "normalTexture": "texture/defaults/default_nml.rpak",
    "glossTexture": "texture/defaults/default_gls.rpak",
    "specTexture": "texture/defaults/default_spc.rpak",
    "cavityTexture": "texture/defaults/default_cav_black.rpak",
    "emissiveTexture": "texture/defaults/default_selfillum.rpak",
    "opacityMultiplyTexture": "texture/effects/white.rpak",
}

# Stock S21 blend arrays keyed by slot 0. RT3 of the opaque pass is R32_UINT, so
# slot 3 must never blend or the PSO silently fails to create; TF2 enables it.
BLEND_CANON = {
    0xF0138286: (0xF0138286, 0xF0138286, 0xF0008286, 0xF0000000, 0xF0138286, 0, 0x80500002, 0),
    0xF0138086: (0xF0138086, 0x00138286, 0xF0008286, 0xF0000000, 0xF0138086, 0, 0, 0),
    0xF0138292: (0xF0138292, 0xF0138286, 0xF0008286, 0xF0000000, 0xF0138292, 0, 0x80500002, 0),
}


DEPTH_ONLY_PASSES = ("_prepass", "_shadow", "_tightshadow")
DEPTH_WRITE = 0x10


def is_translucent(states):
    return g(states[0]) in BLEND_CANON


def s21_blend_states(states, name):
    """Stock families: translucent canon, depth-only passes 0 x8, all else F0000000 x8. RT4..6
    feed screen passes (indirect diffuse merged after SSAO, SSRT inputs); a zero write mask
    there leaves another surface's data under the pixel."""
    vals = [g(v) for v in states]
    canon = BLEND_CANON.get(vals[0])
    if canon:
        return ["0x%X" % v for v in canon], None
    if any(v & 2 for v in vals):
        return None, "blend enabled outside the canon translucent arrays: %s" % ["0x%X" % v for v in vals]
    word = 0 if name.endswith(DEPTH_ONLY_PASSES) else 0xF0000000
    return ["0x%X" % word] * 8, None


def s21_depth_stencil(flags, states):
    """No stock S21 translucent writes depth (bit 4 = DepthWriteMask); TF2 water does."""
    v = g(flags)
    if is_translucent(states):
        v &= ~DEPTH_WRITE
    return "0x%X" % v


# S21 uber members whose TF2 predecessor had another name.
RENAMED = {"FogScale": "fogColorFactor"}


class Converter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tf2 = {}
        for root in cfg["tf2_matl"]:
            for j in load_json_tree(root):
                self.tf2.setdefault(mat_guid(j["name"], j["shaderType"]), j)
        self.s21 = []
        for root in cfg["s21_matl"]:
            self.s21.extend(load_json_tree(root))
        self.s21_by_set = collections.defaultdict(list)
        for j in self.s21:
            self.s21_by_set[g(j.get("shaderSet", "0x0"))].append(j)
        self.shaders = Shaders(cfg["tf2_shaders"] + cfg["s21_shaders"])
        self.shaders.add_children(cfg.get("s21_adj", []))
        self.s21_sets = shaderset_twin.s21_guids(cfg["s21_lists"])
        self.tf2_set_names = {}
        for f in cfg["tf2_lists"]:
            for r in csv.reader(open(f, encoding="utf-8", errors="replace")):
                if r and r[0] == "shds":
                    self.tf2_set_names[int(r[3], 16)] = r[5]
        self.s21_matl_guids = {}
        for f in os.listdir(cfg["s21_lists"]):
            for r in csv.reader(open(os.path.join(cfg["s21_lists"], f), encoding="utf-8", errors="replace")):
                if r and r[0] == "matl":
                    self.s21_matl_guids[int(r[3], 16)] = (r[4], r[5])
        self.overridden = []
        self.donors = {}
        self.twin_cache = {}
        # Precomputed shaderset_twin.py output: the candidate search is too slow to repeat per run.
        if cfg.get("twins"):
            by_name = {v.lower(): k for k, v in self.tf2_set_names.items()}
            for line in open(cfg["twins"], encoding="utf-8"):
                parts = line.split()
                if len(parts) >= 4 and parts[0] == "TWIN":
                    tf2 = by_name.get(parts[1].lower())
                    if tf2 is not None:
                        self.twin_cache[tf2] = (int(parts[-1], 16), parts[3])
                elif len(parts) == 2 and parts[0] == "NONE":
                    tf2 = by_name.get(parts[1].lower())
                    if tf2 is not None:
                        self.twin_cache[tf2] = None
        self.out = {}
        self.report = collections.Counter()
        self.problems = []
        self.textures = set()

    def twin(self, tf2_set):
        if tf2_set in self.twin_cache:
            return self.twin_cache[tf2_set]
        name = self.tf2_set_names.get(tf2_set)
        res = None
        parts = shaderset_twin.split_name(name) if name else None
        if parts:
            for cand, _ in shaderset_twin.candidates(*parts):
                gid = string_to_guid("shaderset/%s.rpak" % cand)
                if gid in self.s21_sets:
                    res = (gid, cand)
                    break
        self.twin_cache[tf2_set] = res
        return res

    def set_names(self):
        if not hasattr(self, "_set_names"):
            self._set_names = {}
            p = self.cfg.get("s21_shds_names")
            if p:
                for line in open(p, encoding="utf-8"):
                    n = line.strip().replace("\\", "/").split("/")[-1]
                    if n:
                        self._set_names[string_to_guid("shaderset/" + n)] = n[:-5] if n.endswith(".rpak") else n
        return self._set_names

    def donor(self, s21_set):
        """Nearest S21 set of the same type with stock materials and the same uber size."""
        names = self.set_names()
        name = names.get(s21_set)
        if not name:
            return None
        base, typ = name.rsplit("_", 1)
        tokens = set(re.findall(r"[A-Z][a-z]*|\d+", base))
        want = self.shaders.uber_layout(self.shaders.set_ps(s21_set) or 0)
        best = None
        for gid, mats in self.s21_by_set.items():
            n = names.get(gid)
            if not mats or not n or not n.endswith("_" + typ):
                continue
            ul = self.shaders.uber_layout(self.shaders.set_ps(gid) or 0)
            if want and (not ul or ul[0] != want[0]):
                continue
            d = len(tokens ^ set(re.findall(r"[A-Z][a-z]*|\d+", n.rsplit("_", 1)[0])))
            if best is None or d < best[0]:
                best = (d, gid, n)
        return best

    def template(self, s21_set):
        mats = self.s21_by_set.get(s21_set, [])
        if not mats:
            d = self.donor(s21_set)
            if d:
                mats = self.s21_by_set[d[1]]
                self.donors[s21_set] = d
        vals = {}
        for k in TEMPLATE_KEYS:
            c = collections.Counter(str(m.get(k)) for m in mats if k in m)
            if c:
                vals[k] = c.most_common(1)[0][0]
        return vals, mats

    def template_uber(self, mats, size):
        ubers = []
        for m in mats:
            p = m["_file"][:-5] + ".uber"
            if os.path.isfile(p) and os.path.getsize(p) == size:
                ubers.append(open(p, "rb").read())
        if not ubers:
            return None
        out = bytearray(size)
        for o in range(0, size, 4):
            c = collections.Counter(u[o:o + 4] for u in ubers)
            out[o:o + 4] = c.most_common(1)[0][0]
        return bytes(out)

    def convert_uber(self, j, tf2_set, s21_set, mats):
        tf2_ps = self.shaders.set_ps(tf2_set)
        s21_ps = self.shaders.set_ps(s21_set)
        tl = self.shaders.uber_layout(tf2_ps) if tf2_ps else None
        sl = self.shaders.uber_layout(s21_ps) if s21_ps else None
        tf2_uber_path = j["_file"][:-5] + ".uber"
        tf2_uber = open(tf2_uber_path, "rb").read() if os.path.isfile(tf2_uber_path) else None
        if not sl:
            self.problems.append("%s: no S21 uber layout for set 0x%016X (PS %s)" % (j["name"], s21_set, s21_ps and "0x%016X" % s21_ps))
            return None
        size, s21_vars = sl
        out = bytearray(self.template_uber(mats, size) or bytes(size))
        if not tl or tf2_uber is None:
            self.problems.append("%s: TF2 uber or layout missing; template only" % j["name"])
            return bytes(out)
        _, tf2_vars = tl
        for name, off, vsize in s21_vars:
            r = tf2_value_for(name, tf2_vars, tf2_uber, vsize)
            if r is None:
                self.report["uber template:" + name] += 1
                continue
            v, how = r
            if how == "merged":
                x, yzw = v
                if x is not None:
                    out[off:off + 4] = x
                if yzw is not None:
                    out[off + 4:off + 16] = yzw
            elif how == "merged_xyz_w":
                xyz, w = v
                if xyz is not None:
                    out[off:off + 12] = xyz
                if w is not None:
                    out[off + 12:off + 16] = w
            else:
                out[off:off + vsize] = v
            self.report["uber " + how] += 1
        # A non-finite TF2 value is unused padding on the TF2 side; keep the set's value.
        template = self.template_uber(mats, size) or bytes(size)
        for name, off, vsize in s21_vars:
            vals = struct.unpack_from("<%df" % (vsize // 4), out, off)
            if any(not math.isfinite(x) for x in vals) and all(
                    math.isfinite(x) for x in struct.unpack_from("<%df" % (vsize // 4), template, off)):
                out[off:off + vsize] = template[off:off + vsize]
                self.report["uber non-finite TF2 value:" + name] += 1
        self.convert_uv_transforms(j, s21_ps, s21_vars, tf2_vars, tf2_uber, out)
        return bytes(out)

    def convert_uv_transforms(self, j, s21_ps, s21_vars, tf2_vars, tf2_uber, out):
        """TF2 applies uvN as u' = M.uv + T. Where the S21 PS reads the same members in
        another form, rewrite them so the S21 math gives the TF2 result."""
        sv = {n: o for n, o, _ in s21_vars}
        tv = {n: o for n, o, _ in tf2_vars}
        for n, form in sorted(self.shaders.uv_forms(s21_ps).items()):
            keys = ["c_uv%dRotScaleX" % n, "c_uv%dRotScaleY" % n, "c_uv%dTranslate" % n]
            if not all(k in sv and k in tv for k in keys):
                continue
            a, b = struct.unpack_from("<2f", tf2_uber, tv[keys[0]])
            c, d = struct.unpack_from("<2f", tf2_uber, tv[keys[1]])
            t = struct.unpack_from("<2f", tf2_uber, tv[keys[2]])
            if form == "rot":
                # S21: uv' = diag(sx, sy) . R(angle) . (uv - center + scroll * time)
                sx = math.hypot(a, b)
                ang = math.atan2(-b, a) if sx > 0 else 0.0
                sy = c * math.sin(ang) + d * math.cos(ang)
                if abs(c - sy * math.sin(ang)) > 1e-4 or abs(d - sy * math.cos(ang)) > 1e-4:
                    self.problems.append("%s: uv%d matrix is not rotation*scale; nearest rotation/scale used" % (j["name"], n))
                m = ((sx * math.cos(ang), -sx * math.sin(ang)), (sy * math.sin(ang), sy * math.cos(ang)))
                struct.pack_into("<2f", out, sv[keys[0]], ang, sx)
                struct.pack_into("<2f", out, sv[keys[1]], 0.0, sy)
            else:
                m = ((a, b), (c, d))
            det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
            if abs(det) > 1e-8:
                s = ((m[1][1] * t[0] - m[0][1] * t[1]) / det, (m[0][0] * t[1] - m[1][0] * t[0]) / det)
            else:
                # Singular: least-squares along the one direction the matrix keeps.
                r = m[0] if (m[0][0] ** 2 + m[0][1] ** 2) >= (m[1][0] ** 2 + m[1][1] ** 2) else m[1]
                tt = t[0] if r is m[0] else t[1]
                nn = r[0] ** 2 + r[1] ** 2
                s = (r[0] * tt / nn, r[1] * tt / nn) if nn > 0 else (0.0, 0.0)
            struct.pack_into("<2f", out, sv[keys[2]], *s)
            self.report["uv%d %s transform converted" % (n, form)] += 1

    def apply_uber_overrides(self, src, s21_set, uber):
        """Authored member values by name; None keeps that component."""
        ov = src.get("$uberOverrides")
        if not ov or uber is None:
            return uber
        layout = self.shaders.uber_layout(self.shaders.set_ps(s21_set) or 0)
        offs = {n: (o, sz) for n, o, sz in layout[1]} if layout else {}
        out = bytearray(uber)
        for name, vals in ov.items():
            if name not in offs or len(vals) * 4 != offs[name][1]:
                self.problems.append("%s: uber override %s does not fit the set" % (src["name"], name))
                continue
            for i, v in enumerate(vals):
                if v is not None:
                    struct.pack_into("<f", out, offs[name][0] + 4 * i, v)
        return bytes(out)

    def pass_ref(self, ref, s21_type):
        gid = g(ref)
        if gid == 0:
            return "0x0"
        src = self.tf2.get(gid)
        if src is None:
            self.problems.append("pass material 0x%016X not in TF2 exports" % gid)
            return "0x0"
        name = src["name"]
        if name.startswith("code_private/"):
            twin = mat_guid(name, s21_type)
            if twin not in self.s21_matl_guids:
                self.problems.append("no S21 code_private twin for %s_%s" % (name, s21_type))
                return "0x0"
            return "0x%016X" % twin
        return "0x%016X" % self.convert(gid, out_type=s21_type)

    def twin_as(self, tf2_set, s21_type):
        """Twin of a TF2 set in a specific S21 type, via the same-name permutation
        of the TF2 type that maps to it (a skinned-only TF2 material on a rigid model)."""
        name = self.tf2_set_names.get(tf2_set)
        want_tf2 = S21_TO_TF2.get(s21_type)
        if not name or not want_tf2:
            return None
        swapped = re.sub(r"_[a-z]+\.rpak$", "_%s.rpak" % want_tf2, name)
        parts = shaderset_twin.split_name(swapped)
        if parts:
            for cand, _ in shaderset_twin.candidates(*parts):
                if not cand.endswith("_" + s21_type):
                    continue
                gid = string_to_guid("shaderset/%s.rpak" % cand)
                if gid in self.s21_sets:
                    return gid, cand
        return None

    def override(self, tf2_set, s21_type):
        name = (self.tf2_set_names.get(tf2_set) or "").lower()
        o = self.cfg.get("twin_overrides", {}).get("%s|%s" % (name, s21_type))
        if not o:
            return None
        self.overridden.append((name, s21_type, o["set"], o["why"]))
        return int(o["set"], 16), o["set"]

    def slot_layout(self, s21_set, mats):
        """(slot index -> bind name, slot index -> fallback texture) from the set's PS."""
        layout = self.shaders.material_slots(self.shaders.set_ps(s21_set) or 0)
        if not layout:
            return None, None
        fallback = {k: DEFAULT_TEXTURES.get(n, "0x0") for k, n in layout.items()}
        if s21_set not in self.donors:
            best = collections.Counter(tuple(sorted(m.get("$textures", {}))) for m in mats)
            if best:
                keys = best.most_common(1)[0][0]
                for m in mats:
                    if tuple(sorted(m.get("$textures", {}))) == keys:
                        fallback.update({k: v for k, v in m["$textures"].items() if k in layout})
                        break
        return layout, fallback

    def convert(self, tf2_guid, out_type=None):
        src = self.tf2[tf2_guid]
        s21_type = out_type or TYPE_MAP.get(src["shaderType"], src["shaderType"])
        out_guid = mat_guid(src["name"], s21_type)
        if out_guid in self.out:
            return out_guid
        self.out[out_guid] = None
        tf2_set = g(src["shaderSet"])
        natural = TYPE_MAP.get(src["shaderType"], src["shaderType"])
        tw = self.twin(tf2_set) if s21_type == natural else self.twin_as(tf2_set, s21_type)
        overridden = False
        if not tw:
            tw = self.override(tf2_set, s21_type)
            overridden = tw is not None
        if not tw and src.get("$s21ShaderSet"):
            tw = g(src["$s21ShaderSet"]), src["$s21ShaderSet"]
            self.overridden.append((src["name"], s21_type, src["$s21ShaderSet"], src["$why"]))
        if not tw:
            self.problems.append("%s: no S21 twin for %s as %s" % (src["name"], self.tf2_set_names.get(tf2_set), s21_type))
            del self.out[out_guid]
            return out_guid
        s21_set, set_name = tw
        consts, mats = self.template(s21_set)
        m = dict((k, v) for k, v in src.items() if not k.startswith("_") and k not in AUTHORED_KEYS)
        m["shaderType"] = s21_type
        m["shaderSet"] = "0x%016X" % s21_set
        for k, v in consts.items():
            m[k] = v
        m["dxStateUnk28"] = pass_state(src["name"])
        if "blendStates" in m:
            if "depthStencilFlags" in m:
                m["depthStencilFlags"] = s21_depth_stencil(m["depthStencilFlags"], m["blendStates"])
            blend, err = s21_blend_states(m["blendStates"], src["name"])
            if err:
                self.problems.append("%s: %s" % (src["name"], err))
            else:
                m["blendStates"] = blend
        for k in PASS_KEYS:
            if k in src:
                m[k] = self.pass_ref(src[k], s21_type)
        # The S21 PS binds its slots in its own order (TF2 layered sets put layer 2 at
        # t22+, S21 at t1..t8), so TF2 textures move by bind name. Slots TF2 never had
        # take the engine's neutral default; only a slot with no neutral keeps the
        # set's stock texture.
        layout, stock = self.slot_layout(s21_set, mats)
        if layout:
            by_name = {src["$textureTypes"][k]: v for k, v in src.get("$textures", {}).items()
                       if k in src.get("$textureTypes", {})}
            m["$textureTypes"] = layout
            m["$textures"] = {k: by_name.get(n, DEFAULT_TEXTURES.get(n, stock.get(k, "0x0")))
                              for k, n in layout.items()}
            dropped = sorted(set(by_name) - set(layout.values()) - {"unavailable"})
            if dropped:
                self.problems.append("%s: S21 set binds no slot for TF2 %s" % (src["name"], dropped))
        else:
            self.problems.append("%s: set 0x%016X has no PS slot layout; TF2 slots kept" % (src["name"], s21_set))
        for t in m.get("$textures", {}).values():
            self.textures.add(t)
        m["_uber"] = self.apply_uber_overrides(src, s21_set, self.convert_uber(src, tf2_set, s21_set, mats))
        m["_set_name"] = set_name
        self.out[out_guid] = m
        self.report["materials"] += 1
        return out_guid

    def write(self, out_dir):
        for gid, m in self.out.items():
            if m is None:
                continue
            rel = "material/%s_%s" % (m["name"], m["shaderType"])
            p = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            uber = m.pop("_uber")
            m.pop("_set_name")
            json.dump(m, open(p + ".json", "w", encoding="utf-8"), indent=1)
            if uber is not None:
                open(p + ".uber", "wb").write(uber)


def main(argv):
    cfg = json.load(open(argv[0]))
    c = Converter(cfg)
    wanted = [l.strip() for l in open(cfg["wanted"]) if l.strip()]
    missing = 0
    for w in wanted:
        name, typ = w.rsplit("_", 1)
        tf2_type = S21_TO_TF2.get(typ, typ)
        gid = mat_guid(name, tf2_type)
        if gid not in c.tf2:
            # Rigid models can reference a material TF2 only shipped skinned, and vice versa.
            other = {"fix": "skn", "skn": "fix"}.get(tf2_type)
            if other and mat_guid(name, other) in c.tf2:
                c.convert(mat_guid(name, other), out_type=typ)
                continue
            c.problems.append("wanted %s has no TF2 source %s_%s" % (w, name, tf2_type))
            missing += 1
            continue
        c.convert(gid, out_type=typ)
    c.write(cfg["out_dir"])
    json.dump(sorted(c.textures), open(os.path.join(cfg["out_dir"], "textures_needed.json"), "w"), indent=1)
    for k, v in sorted(c.report.items()):
        if not k.startswith("uber template:"):
            print("%-24s %d" % (k, v))
    tmpl = [(k[len("uber template:"):], v) for k, v in c.report.items() if k.startswith("uber template:")]
    print("uber members left at the S21 set value: %d distinct" % len(tmpl))
    print("overrides used: %d" % len(c.overridden))
    for name, typ, s, why in sorted(set(c.overridden)):
        print("   %s as %s -> %s: %s" % (name, typ, s, why))
    for s, (d, gid, n) in sorted(c.donors.items()):
        print("   set 0x%016X has no stock material; template from %s (0x%016X, %d name tokens apart)" % (s, n, gid, d))
    print("problems: %d" % len(c.problems))
    for p in c.problems[:60]:
        print("  ", p)


if __name__ == "__main__":
    main(sys.argv[1:])

"""Reader for Titanfall 2 particle files (DMX binary 5, pcf 2).

    els = load("env_training.pcf")

Each element is {"type", "name", "guid" (16 raw bytes), "attrs"}. Element
references are ("E", index); -1 is null. Binary 5 keeps the element type,
element name and every string attribute in the string table; string arrays
stay inline.
"""
import struct

AT_ELEMENT, AT_INT, AT_FLOAT, AT_BOOL, AT_STRING, AT_VOID, AT_TIME, AT_COLOR = 1, 2, 3, 4, 5, 6, 7, 8
AT_VECTOR2, AT_VECTOR3, AT_VECTOR4, AT_QANGLE, AT_QUATERNION, AT_VMATRIX = 9, 10, 11, 12, 13, 14
ARRAY_BASE = 14


class _Reader:
    def __init__(self, data, pos):
        self.d = data
        self.o = pos

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.o)[0]
        self.o += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def cstr(self):
        e = self.d.index(b"\0", self.o)
        v = self.d[self.o:e].decode("latin-1")
        self.o = e + 1
        return v

    def raw(self, n):
        v = self.d[self.o:self.o + n]
        self.o += n
        return v


class Ref(tuple):
    """("E", index) -- an element reference."""

    def __new__(cls, index):
        return super().__new__(cls, ("E", index))

    @property
    def index(self):
        return self[1]


def load(path):
    data = open(path, "rb").read()
    end = data.index(b"\0")
    header = data[:end].decode("latin-1")
    if "binary" not in header:
        raise ValueError("%s: not a binary DMX file" % path)
    version = int(header.split("binary")[1].split()[0])
    if version < 4:
        raise ValueError("%s: DMX binary %d is not supported" % (path, version))
    r = _Reader(data, end + 1)

    strings = [r.cstr() for _ in range(r.i32())]
    elements = []
    for _ in range(r.i32()):
        etype = strings[r.i32()]
        ename = strings[r.i32()]
        elements.append({"type": etype, "name": ename, "guid": r.raw(16), "attrs": {}})

    def value(t):
        if t == AT_ELEMENT:
            return Ref(r.i32())
        if t == AT_INT:
            return r.i32()
        if t == AT_FLOAT:
            return r.f32()
        if t == AT_BOOL:
            return bool(r.u8())
        if t == AT_STRING:
            return strings[r.i32()] if version >= 5 else r.cstr()
        if t == AT_VOID:
            return r.raw(r.i32())
        if t == AT_TIME:
            return r.i32() / 10000.0
        if t == AT_COLOR:
            return tuple(r.raw(4))
        if t in (AT_VECTOR2, AT_VECTOR3, AT_VECTOR4, AT_QANGLE, AT_QUATERNION):
            n = {AT_VECTOR2: 2, AT_VECTOR3: 3, AT_VECTOR4: 4, AT_QANGLE: 3, AT_QUATERNION: 4}[t]
            return tuple(r.f32() for _ in range(n))
        if t == AT_VMATRIX:
            return tuple(r.f32() for _ in range(16))
        raise ValueError("%s: attribute type %d at 0x%X" % (path, t, r.o))

    for el in elements:
        for _ in range(r.i32()):
            name = strings[r.i32()]
            t = r.u8()
            if t > ARRAY_BASE:
                count = r.i32()
                base = t - ARRAY_BASE
                v = [r.cstr() for _ in range(count)] if base == AT_STRING else [value(base) for _ in range(count)]
            else:
                v = value(t)
            el["attrs"][name] = (t, v)
    if r.o != len(data):
        raise ValueError("%s: %d trailing bytes" % (path, len(data) - r.o))
    return elements


def systems(elements):
    """name -> element index for every particle system definition."""
    return {e["name"]: i for i, e in enumerate(elements) if e["type"] == "DmeParticleSystemDefinition"}


def attr(el, name, default=None):
    a = el["attrs"].get(name)
    return a[1] if a else default

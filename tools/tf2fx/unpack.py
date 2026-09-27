"""Operator and system field tables (DmxElementUnpackStructure_t), dumped from the
engine's static unpack tables into unpack_tables.json, plus value encoding.

A field is {name, default, type, offset, size, ...}; offset is into the runtime
object. Types follow DmAttributeType_t (same numbering as the .pcf file)."""
import json
import os
import struct

import dmx

HERE = os.path.dirname(os.path.abspath(__file__))


class Classes:
    def __init__(self, raw):
        self.raw = raw
        self._by_name = {}
        for cls, d in raw["operators"].items():
            d = dict(d)
            d["class"] = cls
            self._by_name[d["name"].lower()] = d
        self.system = raw["system"]

    def by_name(self, fn):
        return self._by_name.get((fn or "").lower())


def load(path=None):
    return Classes(json.load(open(path or os.path.join(HERE, "unpack_tables.json"))))


def _parse_default(field):
    s = field.get("default")
    if s is None:
        return None
    s = s.strip()
    t = field["type"]
    try:
        if t == dmx.AT_INT:
            return int(float(s))
        if t == dmx.AT_FLOAT:
            return float(s.rstrip("f") or 0)
        if t == dmx.AT_BOOL:
            return bool(int(float(s)))
        if t == dmx.AT_COLOR:
            return tuple(int(x) for x in s.split()[:4])
        if t in (dmx.AT_VECTOR2, dmx.AT_VECTOR3, dmx.AT_VECTOR4, dmx.AT_QANGLE):
            return tuple(float(x.rstrip("f")) for x in s.split())
        if t == dmx.AT_STRING:
            return s
    except ValueError:
        return None
    return None


def value(field, element):
    """The value a .pcf element carries for this field, else the field's default."""
    a = element["attrs"].get(field["name"]) if element is not None else None
    if a is not None:
        return a[1]
    return _parse_default(field)


def encode(field, element):
    """Bytes the engine stores for this field, or None for types that are not plain data."""
    v = value(field, element)
    if v is None:
        return None
    t = field["type"]
    if t == dmx.AT_INT:
        return struct.pack("<i", int(v))
    if t == dmx.AT_FLOAT:
        return struct.pack("<f", float(v))
    if t == dmx.AT_BOOL:
        return bytes([1 if v else 0])
    if t == dmx.AT_COLOR:
        return bytes(v[:4])
    if t in (dmx.AT_VECTOR2, dmx.AT_VECTOR3, dmx.AT_VECTOR4, dmx.AT_QANGLE):
        return struct.pack("<%df" % len(v), *v)
    return None

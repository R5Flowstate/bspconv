"""Machine-local tool locations for the TF2 map pipeline.

Order: environment variable, then paths.json beside this file, then the
default (an exe name found on PATH, where there is one). Copy paths.example.json to paths.json.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.path.join(_HERE, "paths.json")

_DEFAULTS = {
    "repakExe": "repak.exe",
    "rsxExe": "rsx.exe",
    "shaderDisassembler": "",
}

_ENV = {
    "repakExe": "TF2_REPAK_EXE",
    "rsxExe": "TF2_RSX_EXE",
    "shaderDisassembler": "TF2_SHADER_DISASM",
}

_file = {}
if os.path.isfile(_CONFIG):
    with open(_CONFIG, "rb") as f:
        _file = json.loads(f.read().decode("utf-8"))


def get(key):
    v = os.environ.get(_ENV[key]) or _file.get(key) or _DEFAULTS[key]
    if not v:
        raise SystemExit("%s is not set: add it to tf2common/paths.json or set %s" % (key, _ENV[key]))
    return v

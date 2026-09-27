"""TF2 particle corpus: every system definition across the extracted .pcf files,
joined by name to the S21 oracle (s21_corpus.py)."""
import glob
import json
import os
import pickle

import dmx

HERE = os.path.dirname(os.path.abspath(__file__))
LIST_NAMES = ["initializers", "operators", "renderers", "emitters", "forcegenerators", "constraints"]
# DMX list attribute per disk list
DMX_LISTS = {"initializers": "initializers", "operators": "operators", "renderers": "renderers",
             "emitters": "emitters", "forcegenerators": "forces", "constraints": "constraints"}


DATA = os.path.join(HERE, "data")


def load_s21_table():
    return json.load(open(os.path.join(DATA, "s21_table.json")))


class Tf2Corpus:
    def __init__(self, pcf_dir):
        self.files = {}
        self.systems = {}          # name -> (file, element index)
        for p in sorted(glob.glob(os.path.join(pcf_dir, "*.pcf"))):
            els = dmx.load(p)
            f = os.path.basename(p)
            self.files[f] = els
            for name, i in dmx.systems(els).items():
                self.systems.setdefault(name, (f, i))

    def system(self, name):
        f, i = self.systems[name]
        return self.files[f], self.files[f][i]

    def ops(self, name, list_name):
        els, sd = self.system(name)
        refs = dmx.attr(sd, DMX_LISTS[list_name], []) or []
        return [els[r.index] for r in refs if r.index >= 0]

    def children(self, name):
        els, sd = self.system(name)
        out = []
        for r in dmx.attr(sd, "children", []) or []:
            ch = els[r.index]
            target = dmx.attr(ch, "child")
            out.append((ch, els[target.index] if target is not None and target.index >= 0 else None))
        return out


def load_s21(path):
    return pickle.load(open(path, "rb"))

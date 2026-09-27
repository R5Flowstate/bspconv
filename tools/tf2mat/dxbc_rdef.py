"""Dump the constant-buffer layout (RDEF) of every DXBC blob inside a file.

    py dxbc_rdef.py <file.msw> [cbuffer-name-filter]
"""
import struct
import sys


def cstr(b, o):
    return b[o:b.index(b"\0", o)].decode("ascii", "replace")


def parse_rdef(d):
    ncb, cbofs, nbind, bindofs = struct.unpack_from("<4I", d, 0)
    binds = []
    for i in range(nbind):
        name, typ, rtype, dim, samples, point, count, flags = struct.unpack_from("<8I", d, bindofs + 32 * i)
        binds.append((cstr(d, name), typ, point, count))
    cbs = []
    for i in range(ncb):
        name, nvar, varofs, size, flags, cbtype = struct.unpack_from("<6I", d, cbofs + 24 * i)
        vars_ = []
        for k in range(nvar):
            vname, start, vsize, vflags, vtype, dflt = struct.unpack_from("<6I", d, varofs + 40 * k)
            vars_.append((cstr(d, vname), start, vsize))
        cbs.append((cstr(d, name), size, vars_))
    return binds, cbs


def blobs(data):
    i = 0
    while True:
        i = data.find(b"DXBC", i)
        if i < 0:
            return
        total = struct.unpack_from("<I", data, i + 24)[0]
        yield data[i:i + total]
        i += 4


def rdef_of(blob):
    n = struct.unpack_from("<I", blob, 28)[0]
    for k in range(n):
        o = struct.unpack_from("<I", blob, 32 + 4 * k)[0]
        if blob[o:o + 4] == b"RDEF":
            size = struct.unpack_from("<I", blob, o + 4)[0]
            return parse_rdef(blob[o + 8:o + 8 + size])
    return None


def main(argv):
    data = open(argv[0], "rb").read()
    filt = argv[1] if len(argv) > 1 else None
    seen = set()
    for b in blobs(data):
        r = rdef_of(b)
        if not r:
            continue
        binds, cbs = r
        for name, size, vars_ in cbs:
            if filt and filt not in name:
                continue
            key = (name, size, tuple(vars_))
            if key in seen:
                continue
            seen.add(key)
            print("cbuffer %s (%d bytes)" % (name, size))
            for v in vars_:
                print("   +%4d %4d %s" % (v[1], v[2], v[0]))
        if not filt:
            print("binds:", [(n, p) for n, t, p, c in binds])


if __name__ == "__main__":
    main(sys.argv[1:])

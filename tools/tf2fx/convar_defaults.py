"""Read ConVar registrations out of a game DLL: for every lea that loads a
convar-name string, report the other string leas in the same short window
(the ctor takes name, default, flags, help), so name and default are paired by
the call site, not by .rdata ordering.

    py convar_defaults.py <dll> <name regex>
"""
import re
import sys

import capstone
import pefile


def main(path, pattern):
    pe = pefile.PE(path, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    img = pe.get_memory_mapped_image()
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    tva = base + text.VirtualAddress
    code = img[text.VirtualAddress:text.VirtualAddress + text.Misc_VirtualSize]

    def cstr(va):
        rva = va - base
        if rva < 0 or rva >= len(img):
            return None
        end = img.find(b"\0", rva, rva + 300)
        if end < 0:
            return None
        s = img[rva:end]
        return s.decode("latin-1") if s and all(32 <= c < 127 for c in s) else None

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = False
    pat = re.compile(pattern)
    leas = []
    # lea r64, [rip+disp32] = REX(48/4C) 8D modrm(05/0D/15/1D/25/2D/35/3D)
    for m in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", code):
        off = m.start()
        disp = int.from_bytes(code[off + 3:off + 7], "little", signed=True)
        va = tva + off + 7 + disp
        s = cstr(va)
        if s is not None:
            reg = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi"][(code[off + 2] >> 3) & 7]
            if code[off] == 0x4C:
                reg = "r%d" % (8 + ((code[off + 2] >> 3) & 7))
            leas.append((off, reg, s))
    for i, (off, reg, s) in enumerate(leas):
        if not pat.fullmatch(s):
            continue
        near = [(o - off, r, t) for o, r, t in leas[max(0, i - 4):i + 5] if abs(o - off) < 64 and o != off]
        # floats/ints passed as immediates or xmm are not strings; print the call window too
        window = code[max(0, off - 48):off + 64]
        calls = [ins.op_str for ins in md.disasm(window, tva + max(0, off - 48)) if ins.mnemonic == "call"]
        print("%-32s %-4s | %s | calls %s" % (s, reg, "; ".join("%+d %s=%r" % x for x in near), calls[:2]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

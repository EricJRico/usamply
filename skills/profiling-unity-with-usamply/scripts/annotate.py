"""Per-line and per-instruction timings for one native function (Burst job, engine, any DLL).

Usage: python annotate.py PROFILE.json --func MyJob.Execute [--process Unity.exe]
                          [--context 3] [--all] [--dumpbin PATH]

Takes every sample whose leaf (including inlined frames) matches --func, then:
  1. per source line: share of the function's samples, with the source text
  2. instruction mix of the function (divides, sqrt, gathers, calls, ...)
  3. disassembly of the function from the DLL on disk, each instruction with its sample share
     and source line (hot regions only, unless --all)

Skid: a timer sample lands on the instruction that was about to retire, so the cost of a
slow instruction (divide, cache-missing load) usually shows on the 1-3 instructions AFTER it.
Read hot regions, not single instructions.
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys

SLOW = [  # (label, regex on mnemonic)
    ("divide", r"^v?(div|idiv)|^v?div[sp][sd]$|^vdiv"),
    ("sqrt/rsqrt/rcp", r"sqrt|rsqrt|rcp"),
    ("gather", r"gather"),
    ("call", r"^call$"),
    ("int multiply", r"^i?mul$|^imul"),
]


def find_dumpbin(explicit):
    if explicit:
        return explicit
    pats = [r"C:\Program Files\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe",
            r"C:\Program Files (x86)\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe"]
    hits = sorted(h for p in pats for h in glob.glob(p))
    return hits[-1] if hits else None


def pe_info(dll):
    """Image base, timestamp, size of image and sections of a PE32+ file (no dependencies)."""
    import struct
    with open(dll, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec, = struct.unpack_from("<H", data, pe + 6)
    ts, = struct.unpack_from("<I", data, pe + 8)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    base, = struct.unpack_from("<Q", data, opt + 24)
    size_image, = struct.unpack_from("<I", data, opt + 56)
    secs = []
    for k in range(nsec):
        s = opt + opt_size + 40 * k
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, s + 8)
        secs.append((va, max(vsize, raw_size), raw_ptr))
    return data, base, f"{ts:X}", f"{size_image:X}", secs


def disassemble_capstone(dll, start, end):
    try:
        import capstone
    except ImportError:
        sys.exit("\nNo disassembler: install VS C++ tools (dumpbin) or `python -m pip install --user capstone`")
    data, base, _, _, secs = pe_info(dll)
    for va, size, raw in secs:
        if va <= start < va + size:
            code = data[raw + (start - va): raw + (min(end, va + size) - va)]
            break
    else:
        sys.exit(f"\nrva 0x{start:X} not in any section of {dll}")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    return [(i.address, i.mnemonic, i.op_str) for i in md.disasm(code, start)]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout


def collect(p, func, process):
    """Return {(lib_index, rva): count}, {rva: line}, native symbol (addr,size,name), file, total."""
    shared = p.get("shared", {}).get("stringArray")
    counts, lines = collections.Counter(), {}
    symbol, src_file, total = None, None, 0
    needle = func.lower()
    for t in p["threads"]:
        if process and t.get("processName") != process:
            continue
        st = t.get("stringArray") or shared
        fn, fr, sk, rt, ns = t["funcTable"], t["frameTable"], t["stackTable"], t["resourceTable"], t.get("nativeSymbols") or {}
        for si in t["samples"]["stack"]:
            if si is None:
                continue
            leaf = sk["frame"][si]
            addr = fr["address"][leaf]
            # The leaf plus the inline frames that share its address.
            names, s = [], si
            while s is not None and fr["address"][sk["frame"][s]] == addr:
                f = fr["func"][sk["frame"][s]]
                names.append(st[fn["name"][f]])
                s = sk["prefix"][s]
            if not any(needle in n.lower() for n in names):
                continue
            r = fn["resource"][fr["func"][leaf]]
            if r is None or r < 0 or rt["lib"][r] is None or addr is None or addr < 0:
                continue
            lib = rt["lib"][r]
            counts[(lib, addr)] += 1
            total += 1
            if fr["line"][leaf] is not None:
                lines[addr] = fr["line"][leaf]
                fname = fn["fileName"][fr["func"][leaf]]
                if fname is not None and src_file is None:
                    src_file = st[fname]
            nsi = fr.get("nativeSymbol", [None] * fr["length"])[leaf]
            if nsi is not None and symbol is None and ns:
                symbol = (ns["address"][nsi], ns["functionSize"][nsi], st[ns["name"][nsi]])
    return counts, lines, symbol, src_file, total


def image_base(dumpbin, dll):
    h = run([dumpbin, "/headers", dll])
    m = re.search(r"([0-9A-Fa-f]+) image base", h)
    ts = re.search(r"([0-9A-Fa-f]+) time date stamp", h)
    size = re.search(r"([0-9A-Fa-f]+) size of image", h)
    return (int(m.group(1), 16) if m else 0x180000000,
            ts.group(1).upper() if ts else "", size.group(1).upper().lstrip("0") if size else "")


def disassemble(dumpbin, dll, base, start, end):
    out = run([dumpbin, "/disasm:nobytes", f"/range:0x{base + start:X},0x{base + end:X}", dll])
    ins = []
    for line in out.splitlines():
        m = re.match(r"\s*([0-9A-Fa-f]{8,16}):\s+(\S+)\s*(.*)$", line)
        if m:
            ins.append((int(m.group(1), 16) - base, m.group(2), m.group(3).strip()))
    return ins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--func", required=True)
    ap.add_argument("--process", default="Unity.exe")
    ap.add_argument("--context", type=int, default=3, help="instructions shown around hot ones")
    ap.add_argument("--hot", type=float, default=0.5, help="%% of function samples that makes an instruction hot")
    ap.add_argument("--all", action="store_true", help="print the whole function")
    ap.add_argument("--dumpbin")
    ap.add_argument("--disasm", choices=["auto", "dumpbin", "capstone"], default="auto",
                    help="auto = dumpbin if Visual Studio is installed (shows call target names), else capstone")
    a = ap.parse_args()

    with open(a.profile, encoding="utf-8") as f:
        p = json.load(f)
    counts, lines, symbol, src_file, total = collect(p, a.func, a.process)
    if not total:
        sys.exit(f"no leaf samples matching {a.func!r}")
    libs = {lib for lib, _ in counts}
    lib_i = max(libs, key=lambda li: sum(c for (l, _), c in counts.items() if l == li))
    lib = p["libs"][lib_i]
    by_addr = {addr: c for (l, addr), c in counts.items() if l == lib_i}
    interval = p["meta"].get("interval", 1)

    print(f"{a.func}: {total} self samples (~{total * interval:.0f} ms) in {lib['name']}")
    if symbol:
        print(f"symbol: {symbol[2][:160]}  rva 0x{symbol[0]:X} size {symbol[1]}")

    # 1. per source line
    per_line = collections.Counter()
    for addr, c in by_addr.items():
        per_line[lines.get(addr)] += c
    src = []
    if src_file and os.path.exists(src_file):
        with open(src_file, encoding="utf-8", errors="replace") as f:
            src = f.read().splitlines()
    print(f"\n== per source line ({src_file or 'no line info'})")
    for ln, c in per_line.most_common(15):
        text = src[ln - 1].strip() if ln and 0 < ln <= len(src) else ""
        print(f"  {100 * c / total:5.1f}%  {ln if ln else '?':>5}  {text[:110]}")

    # 2/3. disassembly
    dll = lib["path"]
    if not os.path.exists(dll):
        sys.exit(f"\n{dll} no longer on disk; can't disassemble (Burst cache rebuilt?)")
    dumpbin = None if a.disasm == "capstone" else find_dumpbin(a.dumpbin)
    if a.disasm == "dumpbin" and not dumpbin:
        sys.exit("dumpbin.exe not found; pass --dumpbin or use --disasm capstone")
    if dumpbin:
        base, ts, size = image_base(dumpbin, dll)
    else:
        _, base, ts, size, _ = pe_info(dll)
    code_id = (lib.get("codeId") or "").upper()
    if code_id and ts and not code_id.startswith(ts.lstrip("0")):
        print(f"\nWARNING: {dll} on disk (timestamp {ts}) doesn't match the profiled build (codeId {code_id}).")
    if symbol and symbol[1]:
        start, end = symbol[0], symbol[0] + symbol[1]
    else:
        start, end = min(by_addr) - 64, max(by_addr) + 64
    ins = disassemble(dumpbin, dll, base, start, end) if dumpbin else disassemble_capstone(dll, start, end)
    if not ins:
        sys.exit("\ndumpbin returned no instructions")

    mix = collections.Counter()
    for _, mnem, _ in ins:
        for label, rx in SLOW:
            if re.search(rx, mnem):
                mix[label] += 1
    print(f"\n== instruction mix: {len(ins)} instructions" +
          ("".join(f", {n} {k}" for k, n in mix.items()) if mix else ""))

    # Carry the last known source line forward for unsampled instructions (marked ~).
    hot_idx = {i for i, (rva, _, _) in enumerate(ins) if 100 * by_addr.get(rva, 0) / total >= a.hot}
    show = set(range(len(ins))) if a.all else {j for i in hot_idx for j in range(i - a.context, i + a.context + 1)}
    print(f"\n== disassembly ({'all' if a.all else f'instructions >= {a.hot}% plus {a.context} around them'};"
          " cost often shows 1-3 instructions after the slow one)")
    last_line, prev = None, -2
    for i, (rva, mnem, ops) in enumerate(ins):
        ln = lines.get(rva)
        line_txt = f"{ln:5}" if ln else (f"~{last_line:4}" if last_line else "     ")
        if ln:
            last_line = ln
        if i not in show:
            continue
        if i != prev + 1:
            print("  " + "-" * 60)
        c = by_addr.get(rva, 0)
        pct = f"{100 * c / total:5.1f}%" if c else "      "
        slow = " <" if any(re.search(rx, mnem) for _, rx in SLOW) else ""
        print(f"  {pct} {line_txt}  +{rva - start:05X}  {mnem:10} {ops[:70]}{slow}")
        prev = i


if __name__ == "__main__":
    main()

"""Symbol coverage per DLL for a usamply profile: which modules are missing symbols.

Usage: python coverage.py PROFILE.json [--process Unity.exe] [--min-samples 20]

For every module that appears in sampled stacks, prints how many samples touch it and
what share of those frames have a real name. Frames named fun_<hex> (made up from unwind
info, no PDB) or 0x<hex> (nothing at all) count as unnamed. Code that is in no module
(JIT code missing from the rundown, stubs/trampolines) is reported as "(no module)".
"""
import argparse
import collections
import json


def unnamed(name):
    return name.startswith("fun_") or name.startswith("0x")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--process", default="Unity.exe")
    ap.add_argument("--min-samples", type=int, default=20)
    a = ap.parse_args()

    with open(a.profile, encoding="utf-8") as f:
        p = json.load(f)
    shared = p.get("shared", {}).get("stringArray")
    libs = p.get("libs", [])

    touch = collections.Counter()      # samples whose stack contains the module
    leaf = collections.Counter()       # samples whose leaf frame is in the module
    unnamed_leaf = collections.Counter()
    frames = collections.Counter()     # distinct frames seen per module
    unnamed_frames = collections.Counter()
    total = 0

    for t in p["threads"]:
        if a.process and t.get("processName") != a.process:
            continue
        st = t.get("stringArray") or shared
        fn, fr, sk, rt = t["funcTable"], t["frameTable"], t["stackTable"], t["resourceTable"]

        def module(fi):
            r = fn["resource"][fr["func"][fi]]
            if r is None or r < 0:
                return "(no module)"
            li = rt["lib"][r]
            return libs[li]["path"] if li is not None else st[rt["name"][r]]

        seen_frames = set()
        for si in t["samples"]["stack"]:
            if si is None:
                continue
            total += 1
            mods = set()
            first = True
            while si is not None:
                fi = sk["frame"][si]
                m = module(fi)
                name = st[fn["name"][fr["func"][fi]]]
                mods.add(m)
                if first:
                    leaf[m] += 1
                    unnamed_leaf[m] += unnamed(name)
                    first = False
                if (id(t), fi) not in seen_frames:
                    seen_frames.add((id(t), fi))
                    frames[m] += 1
                    unnamed_frames[m] += unnamed(name)
                si = sk["prefix"][si]
            for m in mods:
                touch[m] += 1

    print(f"{a.profile}: {total} samples in {a.process or 'all processes'}\n")
    print(f"{'in stack':>9} {'leaf':>7} {'unnamed leaf':>13} {'unnamed frames':>15}  module")
    rows = sorted(touch.items(), key=lambda kv: -kv[1])
    for m, n in rows:
        if n < a.min_samples:
            continue
        uf = 100 * unnamed_frames[m] / frames[m] if frames[m] else 0
        ul = 100 * unnamed_leaf[m] / leaf[m] if leaf[m] else 0
        flag = "  <-- missing symbols" if uf > 50 else ""
        print(f"{100 * n / total:8.1f}% {100 * leaf[m] / total:6.1f}% {ul:12.0f}% {uf:14.0f}%  {m}{flag}")


if __name__ == "__main__":
    main()

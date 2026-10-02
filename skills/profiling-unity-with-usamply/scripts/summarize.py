"""Summarize a usamply/samply processed profile (Firefox profiler JSON) as text.

Usage: python summarize.py PROFILE.json [--top 25] [--thread SUBSTR] [--min-samples 50] [--focus TEXT]

Prints, per busy thread: sample count, top functions by self time and by total
(inclusive) time, and the share of samples whose leaf is managed (C#) code.
"""
import argparse
import collections
import json
import sys


def col(table, name, n):
    v = table.get(name)
    return v if v is not None else [None] * n


def summarize_thread(t, libs, top, shared_strings, focus=""):
    st = t.get("stringArray") or shared_strings  # per-thread before format v53, shared after
    nsyms = t.get("nativeSymbols") or {}
    samples, stacks, frames, funcs, res = (
        t["samples"], t["stackTable"], t["frameTable"], t["funcTable"], t["resourceTable"])
    n_samples = samples["length"]
    if not n_samples:
        return None
    weights = col(samples, "weight", n_samples)
    s_prefix, s_frame = stacks["prefix"], stacks["frame"]
    f_func = frames["func"]
    f_nsym = col(frames, "nativeSymbol", frames["length"])
    fn_name, fn_res = funcs["name"], funcs["resource"]
    r_lib = col(res, "lib", res["length"])

    frame_label = {}

    def label(fi):
        if fi in frame_label:
            return frame_label[fi]
        func = f_func[fi]
        name = st[fn_name[func]]
        ri = fn_res[func]
        lib_i = r_lib[ri] if ri is not None and ri >= 0 else None
        lib = libs[lib_i] if lib_i is not None else None
        ns = f_nsym[fi]
        if name.startswith("0x") and ns is not None and nsyms.get("name"):
            name = st[nsyms["name"][ns]]
        libname = lib["name"] if lib else (st[res["name"][ri]] if ri is not None and ri >= 0 else "?")
        managed = "JIT" in libname  # usamply labels Mono/CoreCLR JIT code "CoreCLR JIT"
        frame_label[fi] = (f"{name}  [{libname}]", managed)
        return frame_label[fi]

    self_c, total_c = collections.Counter(), collections.Counter()
    managed_leaf = 0
    total = 0
    for i in range(n_samples):
        si = samples["stack"][i]
        w = weights[i] or 1
        total += w
        if si is None:
            continue
        chain = []
        while si is not None:
            chain.append(label(s_frame[si]))
            si = s_prefix[si]
        if focus:
            # Keep only samples under a matching frame, counting that frame and its callees.
            cut = next((k for k, (lab, _) in enumerate(chain) if focus.lower() in lab.lower()), None)
            if cut is None:
                total -= w
                continue
            chain = chain[:cut + 1]
        leaf, is_managed = chain[0]
        self_c[leaf] += w
        managed_leaf += w if is_managed else 0
        for lab in {lab for lab, _ in chain}:
            total_c[lab] += w
    return total, managed_leaf, self_c.most_common(top), total_c.most_common(top)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--thread", default="")
    ap.add_argument("--min-samples", type=int, default=50)
    ap.add_argument("--focus", default="", help="only time spent under a frame containing this text, e.g. MyFeature.Update")
    a = ap.parse_args()

    with open(a.profile, encoding="utf-8") as f:
        p = json.load(f)
    libs = p.get("libs", [])
    interval = p["meta"].get("interval", 1)
    print(f"profile: {a.profile}  format v{p['meta'].get('preprocessedProfileVersion')}  interval {interval} ms")

    rows = []
    for t in p["threads"]:
        if a.thread and a.thread.lower() not in t["name"].lower():
            continue
        r = summarize_thread(t, libs, a.top, p.get("shared", {}).get("stringArray"), a.focus)
        if r and r[0] >= a.min_samples:
            rows.append((t, r))
    rows.sort(key=lambda x: -x[1][0])

    for t, (total, managed, top_self, top_total) in rows:
        print(f"\n=== {t['name']} (tid {t.get('tid')})  {total} samples ≈ {total * interval:.0f} ms  "
              f"managed-leaf {100 * managed / total:.1f}%")
        print("  -- self --")
        for name, c in top_self:
            print(f"  {100 * c / total:5.1f}%  {name}")
        print("  -- total (inclusive) --")
        for name, c in top_total:
            print(f"  {100 * c / total:5.1f}%  {name}")
    if not rows:
        print("no threads matched (lower --min-samples or change --thread)", file=sys.stderr)


if __name__ == "__main__":
    main()

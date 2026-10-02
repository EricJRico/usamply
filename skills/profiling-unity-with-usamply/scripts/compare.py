"""Compare two usamply profiles of the same workload: what got faster or slower.

Usage: python compare.py BEFORE.json AFTER.json --focus MyFeature.Update [--thread Unity.exe] [--top 20]

With --focus, only time under the matching frame counts (use the feature's entry point).
Prints the focused total for both runs, then the functions whose self and inclusive time
changed most, in ms (1 sample = 1 ms at the default 1 kHz rate). Run the same workload with
the same frame count for both profiles, or the numbers aren't comparable.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from summarize import summarize_thread  # noqa: E402


def load(path, thread, focus):
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    interval = p["meta"].get("interval", 1)
    best = None
    for t in p["threads"]:
        if thread and thread.lower() not in t["name"].lower():
            continue
        r = summarize_thread(t, p.get("libs", []), None, p.get("shared", {}).get("stringArray"), focus)
        if r and (best is None or r[0] > best[0]):
            best = r
    if best is None:
        sys.exit(f"{path}: no samples under --focus {focus!r}")
    total, _, self_c, total_c = best
    return total * interval, {k: v * interval for k, v in self_c}, {k: v * interval for k, v in total_c}


def table(title, before, after, top):
    keys = set(before) | set(after)
    rows = sorted(((after.get(k, 0) - before.get(k, 0), k) for k in keys), key=lambda r: -abs(r[0]))[:top]
    print(f"\n  -- {title} (ms: before -> after, delta) --")
    for d, k in rows:
        print(f"  {before.get(k, 0):8.0f} -> {after.get(k, 0):8.0f}  {d:+8.0f}  {k[:150]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--focus", default="")
    ap.add_argument("--thread", default="Unity.exe")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    tb, sb, ib = load(a.before, a.thread, a.focus)
    ta, sa, ia = load(a.after, a.thread, a.focus)
    pct = 100 * (ta - tb) / tb if tb else 0
    print(f"focus {a.focus or '(whole thread)'}: {tb:.0f} ms -> {ta:.0f} ms ({pct:+.1f}%)")
    table("self time", sb, sa, a.top)
    table("inclusive time", ib, ia, a.top)


if __name__ == "__main__":
    main()

---
name: profiling-unity-with-usamply
description: Use when a Unity feature, system, or scene is slow, stutters, or needs optimizing on Windows ("X runs badly", "make Y faster", "why is this frame slow"), when comparing performance before/after a change, or when the user mentions usamply, samply, or CPU profiling of a Unity Editor.
---

# Optimizing Unity code with usamply

## Overview

The user describes a slow feature. Claude does the rest: picks a repeatable workload, measures a baseline, finds the hotspot down to the source line and instruction (C#, Burst, engine), changes the code, re-measures, and reports a verified result. usamply samples the live Editor through ETW, and the Unity CLI (`unity command ...`) drives the Editor.

**Never claim a speedup without before/after numbers from the same workload. Never change code before a baseline exists.**

**Project-specific notes:** if the project has its own profiling skill or notes (which workload to run, which method to focus on, which output fields prove correctness), load and follow them alongside this skill.

## Prerequisites (verify once per session)

| Need | Check |
|---|---|
| usamply from this repository | `usamply.exe --version`. `record.ps1` looks for `USAMPLY_EXE`, then this repo's `target\release\usamply.exe`, then `PATH`. Build with `cargo build --release -p usamply`. This build makes `-d` work with `-p` on Windows and names Unity Mono C# frames. |
| Connected Editor | `unity status --format json` lists the project with state `ready` (needs the `com.unity.pipeline` package) |
| xperf | Windows Performance Toolkit (Windows SDK) |
| Python 3 | For the analysis scripts. `annotate.py` also needs VS C++ tools (`dumpbin`) or `python -m pip install --user capstone`. |

## The loop

1. **Map the feature to code and a workload.** Find the feature's systems in the project. Then find a repeatable way to exercise it:
   - a project command: `unity command --project-path <proj> --query <keyword> --detail full`. Read its C# to learn the required arguments, which scene must be playing, and what it writes;
   - a scene plus `editor_play` and a fixed duration;
   - if there is none, ask the user how to trigger the feature. Don't invent a workload that might not exercise it.
2. **Find the entry frame:** the C# method that wraps the feature. Use it as `--focus`.
3. **Baseline, twice.** The user gets one UAC prompt per run; tell them first.
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File <skill>\scripts\profile-run.ps1 `
     -ProjectPath <proj> -Out <scratch>\base1.json -Seconds 40 -Workload "<unity command args>"
   ```
   Two runs give the noise floor. In testing, identical runs differed by about 5% in profile time and about 2% in median frame timings.
4. **Diagnose:**
   - `python <skill>\scripts\summarize.py base1.json --focus <entry> --top 30` for hotspots;
   - `python <skill>\scripts\coverage.py base1.json` to check that the hot modules have names;
   - `python <skill>\scripts\annotate.py base1.json --func <hot function> [--hot 4] [--all]` for time per source line and per instruction, plus the instruction mix (divides, sqrt, calls).

   Read the source of the hot lines. Look for the cause: algorithm, allocations, dependent chains of slow instructions, main thread waiting on jobs (`JobHandle.Complete` → `ujob_wait_for`), poor parallelism, or work that could be skipped. To learn *why* Burst emitted something, use the Burst Inspector (or a project command wrapping it) with `IRPassAnalysis`.
5. **Change one thing.** Keep the diff small and state the hypothesis.
6. **Measure again** with the identical workload:
   `python <skill>\scripts\compare.py base1.json after1.json --focus <entry>`
   Also compare the workload's own output (timings, correctness values) if it writes any.
7. **Keep or revert.** Keep the change only if it beats the noise floor and correctness is unchanged. Otherwise revert and try the next hypothesis.
8. **Report:** the hotspot and its share of time, the change, before → after numbers, correctness, and what's still unexplained. Don't commit unless asked.

## Measurement traps

- **Editor overhead:** Burst safety checks, the job debugger and debug code run in the Editor. With them on, Burst and job code is slower than in a player and hotspots can shift. Many `call`s to `NativeArray` plus `and edx,0FFFFFFF9h` in `annotate.py` output are safety checks. Say so in the report; for final numbers, offer a run with safety checks off or a player build.
- **Skid:** a timer sample lands 1–3 instructions **after** the slow one (divide, sqrt, cache-missing load). Read hot regions, not single instructions. Timer sampling can't separate a slow divide from a cache miss.
- **Same workload only:** identical arguments and frame counts. Verify that the workload actually ran the requested length.
- **`--focus` matters:** a whole recording includes Editor UI, idle waits and other threads.
- **Unnamed modules:** GPU drivers, XR runtimes and some kernel drivers have no public symbols. Report their share; don't guess at what they do.

## How C# frames get named

Unity's Mono doesn't list already-compiled methods when a trace starts, but it lists every live method when the trace stops (end rundown, `Method/DCStopVerbose`). usamply applies that list to the whole recording, so you can attach at any time: no reload, no restart. If `profile-run.ps1` warns that there were no end-rundown events, the usamply build is too old; rebuild it, or pass `-Reload` (forces a domain reload while recording).

Native symbols: `record.ps1` passes the Editor folder (Unity and Mono PDBs ship there), `Library/BurstCache/JIT` (Burst DLLs with PDBs), and the Microsoft and Unity symbol servers. Without these, usamply finds only the build-machine PDB paths baked into the DLLs.

## Reading frames

| Label | Meaning |
|---|---|
| `Name [Namespace] 〈sig〉 [CoreCLR JIT]` | C# (Unity's Mono JIT; usamply labels it CoreCLR) |
| `Ns.Job.Execute() [<hash>.dll]` | Burst job (`Library/BurstCache/JIT`) |
| `Foo::Bar(...) [Unity.dll]` | Unity engine native code |
| `ZwWaitForAlertByThreadId`, `ujob_wait_for` | Thread waiting (main thread blocked on jobs) |
| `fun_<hex>`, `0x<hex>` | No symbols |

## Scripts

| Script | Use |
|---|---|
| `profile-run.ps1` | Full run: stop Play, start recorder (UAC), Play, workload, wait. `-KeepEtl`, `-SymbolDir`, `-Reload` |
| `record.ps1` | Record any pid: `-ProcessId <pid> -Seconds N -Out x.json`. For players, pass PDB folders with `-SymbolDir` (`GameAssembly.pdb`, `*_BurstDebugInformation_DoNotShip`) |
| `summarize.py` | Hotspots; `--focus`, `--thread`, `--top` |
| `compare.py` | Before/after deltas under `--focus` |
| `coverage.py` | Per-module symbol coverage |
| `annotate.py` | Per-line and per-instruction time for one function (`--disasm dumpbin` or `capstone`) |
| `send-ctrlc.ps1` | Gracefully stop a hung recording |

## Common mistakes

- `unity command X key=value` is rejected; use flags. Inline C# for `eval` gets mangled by PowerShell; write a `.cs` file and use `eval_file --file`.
- A workload whose service lives in a scene must run in Play mode in that scene, or it fails with "not registered".
- Commands with optional-looking string arguments can still require them; read the C#.
- `-Workload` must be one space-separated string.
- Recording writes about 10 MB/s of ETL; keep runs to 30–90 s. Killing the recorder's shell leaves orphan ETL files; check `xperf -loggers` for `SamplySession`.

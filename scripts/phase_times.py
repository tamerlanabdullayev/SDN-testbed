#!/usr/bin/env python3
"""
phase_times.py
Usage: python3 phase_times.py --rundir <path/to/results/raw/RUN_ID>

Merges <rundir>/phase_times.tsv (lines "name<TAB>start|end<TAB>epoch",
appended by run_experiment.sh and run_mininet_iperf.py) into
<rundir>/phase_times.json, prints a per-phase table, then removes the TSV.
A phase with no end mark (e.g. the step crashed) gets duration_s = null.
"""

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    args = ap.parse_args()
    rundir = Path(args.rundir)
    tsv = rundir / "phase_times.tsv"
    if not tsv.exists():
        print(f"[PHASE] no {tsv}")
        return

    phases = {}  # name -> {"start": ts, "end": ts}, in first-seen order
    with open(tsv) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3 or parts[1] not in ("start", "end"):
                continue
            try:
                ts = float(parts[2])
            except ValueError:
                continue
            phases.setdefault(parts[0], {})[parts[1]] = ts

    out = []
    for name, ev in phases.items():
        start, end = ev.get("start"), ev.get("end")
        dur = round(end - start, 3) if start is not None and end is not None else None
        out.append({"name": name, "start": start, "end": end, "duration_s": dur})

    starts = [p["start"] for p in out if p["start"] is not None]
    ends = [p["end"] for p in out if p["end"] is not None]
    total = round(max(ends) - min(starts), 3) if starts and ends else None

    with open(rundir / "phase_times.json", "w") as f:
        json.dump({"run_id": rundir.name, "phases": out, "total_s": total}, f, indent=2)

    print(f"[PHASE] Phase times for {rundir.name}:")
    for p in out:
        d = f"{p['duration_s']:9.2f}s" if p["duration_s"] is not None else "   (no end)"
        print(f"[PHASE]   {p['name']:<20s}{d}")
    if total is not None:
        print(f"[PHASE]   {'total':<20s}{total:9.2f}s")

    tsv.unlink()


if __name__ == "__main__":
    main()

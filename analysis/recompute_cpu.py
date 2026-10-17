#!/usr/bin/env python3
"""
recompute_cpu.py
Usage: python3 analysis/recompute_cpu.py [--root results --root results_osken]

Recomputes per-run controller CPU from raw system_metrics.csv, dropping the
FIRST sample, and writes <root>/summary/cpu_recomputed.csv. summary_v2.csv is
not modified.

Why: log_system_metrics.py (before the fix) took its first sample immediately
after priming psutil, so the first window lasted microseconds. Process CPU time
advances in 10 ms clock ticks, so that sample is either 0 or a huge value
(thousands of %). Dropping it is equivalent to the fixed monitor, which sleeps
one interval before its first sample.

Output columns:
  run_id                   as in summary_v2.csv (only runs listed there)
  avg_cpu_percent_raw      mean of all samples (= summary_v2.csv avg_cpu_percent)
  avg_cpu_percent_nofirst  mean of all samples except the first
  n_samples                number of samples in system_metrics.csv
  first_sample             value of the dropped first sample
"""

import argparse
import csv
import sys
from pathlib import Path

COLUMNS = ["run_id", "avg_cpu_percent_raw", "avg_cpu_percent_nofirst",
           "n_samples", "first_sample"]


def read_cpu(path: Path):
    vals = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                vals.append(float(row["cpu_percent"]))
            except (KeyError, ValueError):
                continue
    return vals


def recompute(root: Path):
    summary = root / "summary" / "summary_v2.csv"
    if not summary.exists():
        print(f"[ERROR] {summary} not found", file=sys.stderr)
        return False
    with open(summary) as f:
        run_ids = [r["run_id"] for r in csv.DictReader(f)]

    out_rows, missing, short = [], [], []
    for rid in run_ids:
        path = root / "raw" / rid / "system_metrics.csv"
        if not path.exists():
            missing.append(rid)
            continue
        cpu = read_cpu(path)
        if len(cpu) < 2:
            short.append(rid)
            continue
        out_rows.append({
            "run_id": rid,
            "avg_cpu_percent_raw": round(sum(cpu) / len(cpu), 2),
            "avg_cpu_percent_nofirst": round(sum(cpu[1:]) / (len(cpu) - 1), 2),
            "n_samples": len(cpu),
            "first_sample": cpu[0],
        })

    out = root / "summary" / "cpu_recomputed.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out_rows)

    n_first = sum(1 for r in out_rows if r["first_sample"] > 0)
    print(f"[CPU] {out}: {len(out_rows)} runs "
          f"({len(missing)} without system_metrics.csv, {len(short)} with <2 samples); "
          f"first sample > 0 in {n_first} runs")
    for label, ids in (("missing", missing), ("short", short)):
        if ids:
            print(f"[CPU]   {label}: " + ", ".join(ids[:20]))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append",
                    help="Result tree (repeatable); default: results and results_osken")
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[1]
    roots = [Path(r) for r in args.root] if args.root else [repo / "results", repo / "results_osken"]
    ok = all([recompute(r) for r in roots])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

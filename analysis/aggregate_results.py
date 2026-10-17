#!/usr/bin/env python3
"""
aggregate_results.py
Usage: python3 aggregate_results.py --rundir <path/to/results/raw/RUN_ID>
                                     --summary <path/to/results/summary/summary.csv>

Reads per-run CSVs and iperf JSONs, computes aggregate metrics,
writes <rundir>/iperf_summary.json (flow outcome counts, kept after
iperf_json/ is deleted), appends one row to the summary CSV, then exits.

Refuses to append (exit 2) if the summary CSV exists with a different header,
so new-schema rows are never written under the old header.

Throughput columns:
  total_throughput_bps      legacy: sum of per-flow mean rates over all parsed
                            JSONs (unchanged; NOT aggregate throughput)
  delivered_throughput_bps  sum(end.sum_sent.bytes) * 8 / total_time over
                            successful flows. Flows started in the last
                            flow_duration seconds finish after total_time, so
                            this slightly overstates the rate within the window.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True, help="Path to a single run's output dir")
    ap.add_argument("--summary", required=True, help="Path to summary.csv (created if absent)")
    return ap.parse_args()


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_controller_metrics(rundir: Path):
    """Returns avg packet_in_rate and avg flow_mod_rate (counts per second)."""
    path = rundir / "controller_metrics.csv"
    if not path.exists():
        return None, None
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "packet_in_count": float(row["packet_in_count"]),
                    "flow_mod_count":  float(row["flow_mod_count"]),
                })
            except (KeyError, ValueError):
                continue
    if not rows:
        return None, None
    avg_pi  = sum(r["packet_in_count"] for r in rows) / len(rows)
    avg_fm  = sum(r["flow_mod_count"]  for r in rows) / len(rows)
    return avg_pi, avg_fm


def read_flow_events(rundir: Path):
    """Returns p50 and p95 of proc_time_ms."""
    path = rundir / "flow_events.csv"
    if not path.exists():
        return None, None
    times = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                times.append(float(row["proc_time_ms"]) if row["proc_time_ms"] not in (None, "", "None") else None)
            except (KeyError, ValueError):
                continue
    if not times:
        return None, None
    times = [t for t in times if t is not None]
    if not times:
        return None, None
    times.sort()
    n = len(times)
    p50 = times[int(n * 0.50)]
    p95 = times[min(int(n * 0.95), n - 1)]
    return p50, p95


def read_system_metrics(rundir: Path):
    """Returns avg CPU (%) and avg memory (MB)."""
    path = rundir / "system_metrics.csv"
    if not path.exists():
        return None, None
    cpu_vals, mem_vals = [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cpu_vals.append(float(row["cpu_percent"]))
                mem_vals.append(float(row["rss_bytes"]) / (1024 * 1024))  # convert to MB
            except (KeyError, ValueError):
                continue
    if not cpu_vals:
        return None, None
    return sum(cpu_vals) / len(cpu_vals), sum(mem_vals) / len(mem_vals)


def read_traffic_meta(rundir: Path):
    path = rundir / "traffic_meta.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def read_iperf_jsons(rundir: Path):
    """
    Walks iperf_json/ and aggregates:
      - total throughput (bits/sec) summed across all flows
      - total retransmissions
      - flow count
    Returns (total_bps, total_retrans, flow_count).
    """
    iperf_dir = rundir / "iperf_json"
    if not iperf_dir.exists():
        return None, None, 0

    total_bps = 0.0
    total_retrans = 0
    flow_count = 0

    for jf in sorted(iperf_dir.glob("flow_*.json")):
        if "stderr" in jf.name:
            continue
        try:
            with open(jf) as f:
                data = json.load(f)
            # iperf3 JSON structure: data["end"]["sum_sent"]
            sent = data.get("end", {}).get("sum_sent", {})
            bps = sent.get("bits_per_second", 0.0)
            retrans = sent.get("retransmits", 0)
            total_bps += bps
            total_retrans += retrans
            flow_count += 1
        except Exception:
            continue  # skip malformed files

    return total_bps, total_retrans, flow_count


def classify_iperf_jsons(rundir: Path):
    """
    Classifies every iperf3 client JSON as ok / busy / other_error / malformed.
    ok = no "error" field and end.sum_sent present.
    Returns a dict, or None if iperf_json/ is absent.
    """
    iperf_dir = rundir / "iperf_json"
    if not iperf_dir.exists():
        return None

    counts = Counter(ok=0, busy=0, other_error=0, malformed=0)
    errors = Counter()
    ok_bytes = 0
    ok_retrans = 0
    n_files = 0

    for jf in sorted(iperf_dir.glob("flow_*.json")):
        if "stderr" in jf.name:
            continue
        n_files += 1
        try:
            with open(jf) as f:
                data = json.load(f)
        except Exception:
            counts["malformed"] += 1
            continue
        err = data.get("error") if isinstance(data, dict) else None
        sent = data.get("end", {}).get("sum_sent") if isinstance(data, dict) else None
        if err:
            errors[str(err)] += 1
            counts["busy" if "busy" in str(err) else "other_error"] += 1
        elif not sent:
            counts["malformed"] += 1
        else:
            counts["ok"] += 1
            ok_bytes += sent.get("bytes", 0)
            ok_retrans += sent.get("retransmits", 0)

    return {
        "json_files": n_files,
        "ok": counts["ok"],
        "busy": counts["busy"],
        "other_error": counts["other_error"],
        "malformed": counts["malformed"],
        "ok_bytes_sent": ok_bytes,
        "ok_retransmits": ok_retrans,
        "top_errors": errors.most_common(5),
    }


# ---------------------------------------------------------------------------
# Summary CSV
# ---------------------------------------------------------------------------

SUMMARY_COLUMNS = [
    "run_id",
    "topology",
    "n_hosts",
    "flows_per_sec",
    "rep",
    "total_time",
    "flow_duration",
    "seed",
    "flow_count",
    "total_throughput_bps",
    "total_retransmits",
    "avg_packet_in_rate",
    "avg_flow_mod_rate",
    "proc_latency_p50_ms",
    "proc_latency_p95_ms",
    "avg_cpu_percent",
    "avg_mem_mb",
    # --- v2 columns (appended; columns above keep their original meaning) ---
    "attempted_flows",
    "successful_flows",
    "failed_flows",
    "success_rate",
    "delivered_throughput_bps",
    "servers_per_host",
    "port_collisions",
    "host_bw_mbit",
    "fq_rate",
    "actual_launch_duration_s",
    "achieved_fps",
]


class SummaryHeaderMismatch(Exception):
    pass


def append_summary(summary_path: Path, row: dict):
    exists = summary_path.exists() and summary_path.stat().st_size > 0
    if exists:
        with open(summary_path, newline="") as f:
            header = next(csv.reader(f), [])
        if header != SUMMARY_COLUMNS:
            raise SummaryHeaderMismatch(
                f"{summary_path} has a different header "
                f"({len(header)} columns, expected {len(SUMMARY_COLUMNS)}); "
                "refusing to append. Use a new summary file (e.g. summary_v2.csv).")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    rundir = Path(args.rundir)
    summary_path = Path(args.summary)

    if not rundir.exists():
        print(f"[ERROR] Run directory not found: {rundir}", file=sys.stderr)
        sys.exit(1)

    run_id = rundir.name

    # Parse run_id: expected format N{n}_F{fps}_R{rep}
    meta = read_traffic_meta(rundir)
    n_hosts       = meta.get("n", None)
    flows_per_sec = meta.get("flows_per_sec", None)
    total_time    = meta.get("total_time", None)
    flow_duration = meta.get("flow_duration", None)
    seed          = meta.get("seed", None)
    topology      = meta.get("topology", "star")

    # Try to extract rep from run_id if not in meta
    rep = None
    for part in run_id.split("_"):
        if part.startswith("R"):
            try:
                rep = int(part[1:])
            except ValueError:
                pass

    # Collect metrics
    avg_pi, avg_fm          = read_controller_metrics(rundir)
    p50, p95                = read_flow_events(rundir)
    avg_cpu, avg_mem        = read_system_metrics(rundir)
    total_bps, retrans, fc  = read_iperf_jsons(rundir)
    outcomes                = classify_iperf_jsons(rundir)

    attempted = successful = failed = success_rate = delivered = None
    if outcomes is not None:
        planned = meta.get("total_flows")
        # Flows that never got a JSON (run aborted) count as attempted+failed.
        attempted = max(planned or 0, outcomes["json_files"])
        successful = outcomes["ok"]
        failed = attempted - successful
        success_rate = successful / attempted if attempted else None
        if total_time:
            delivered = outcomes["ok_bytes_sent"] * 8 / float(total_time)
        outcomes.update({
            "run_id": run_id,
            "planned_flows": planned,
            "missing": attempted - outcomes["json_files"],
            "servers_per_host": meta.get("servers_per_host"),
            "port_collisions": meta.get("port_collisions"),
        })
        with open(rundir / "iperf_summary.json", "w") as f:
            json.dump(outcomes, f, indent=2)

    row = {
        "run_id":               run_id,
        "topology":             topology,
        "n_hosts":              n_hosts,
        "flows_per_sec":        flows_per_sec,
        "rep":                  rep,
        "total_time":           total_time,
        "flow_duration":        flow_duration,
        "seed":                 seed,
        "flow_count":           fc,
        "total_throughput_bps": round(total_bps, 2) if total_bps is not None else "",
        "total_retransmits":    retrans if retrans is not None else "",
        "avg_packet_in_rate":   round(avg_pi, 4) if avg_pi is not None else "",
        "avg_flow_mod_rate":    round(avg_fm, 4) if avg_fm is not None else "",
        "proc_latency_p50_ms":  round(p50, 4) if p50 is not None else "",
        "proc_latency_p95_ms":  round(p95, 4) if p95 is not None else "",
        "avg_cpu_percent":      round(avg_cpu, 2) if avg_cpu is not None else "",
        "avg_mem_mb":           round(avg_mem, 2) if avg_mem is not None else "",
        "attempted_flows":      attempted if attempted is not None else "",
        "successful_flows":     successful if successful is not None else "",
        "failed_flows":         failed if failed is not None else "",
        "success_rate":         round(success_rate, 4) if success_rate is not None else "",
        "delivered_throughput_bps": round(delivered, 2) if delivered is not None else "",
        "servers_per_host":     meta.get("servers_per_host", ""),
        "port_collisions":      meta.get("port_collisions", ""),
        "host_bw_mbit":         meta.get("host_bw_mbit", ""),
        "fq_rate":              meta.get("fq_rate", ""),
        "actual_launch_duration_s": meta.get("actual_launch_duration_s", ""),
        "achieved_fps":         meta.get("achieved_fps", ""),
    }

    try:
        append_summary(summary_path, row)
    except SummaryHeaderMismatch as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
    print(f"[AGG] Appended row for {run_id} → {summary_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
set -euo pipefail

N="${1:-4}"
RUN_ID="${2:-devrun_001}"
PORT=6653
FLOWS_PER_SEC="${3:-25}"
TOTAL_TIME="${4:-20}"
FLOW_DURATION="${5:-2}"
SEED="${6:-1}"
TOPO="${7:-star}"   # topology type: star | tree (alias: fattree) | mesh (experimental)
HOST_BW="${HOST_BW:-0}"     # Mbit/s on host-switch links (env var); 0 = no limit
FQ_RATE="${FQ_RATE:-2M}"    # per-flow iperf3 --fq-rate pacing (env var); 0 = unpaced
KEEP_IPERF_JSON="${KEEP_IPERF_JSON:-0}"   # 1 = keep iperf_json/ after aggregation (env var)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTDIR="$ROOT/results/raw/$RUN_ID"
SUMMARY="$ROOT/results/summary/summary_v2.csv"

mkdir -p "$OUTDIR"
echo "[INFO] OUTDIR=$OUTDIR"
echo "[INFO] N=$N"
echo "[INFO] TOPO=$TOPO"
echo "[INFO] HOST_BW=$HOST_BW"
echo "[INFO] FQ_RATE=$FQ_RATE"

# Phase timing: start/end epoch marks appended here (also by
# run_mininet_iperf.py), merged into phase_times.json at the end.
PHASE_LOG="$OUTDIR/phase_times.tsv"
: > "$PHASE_LOG"
declare -A PHASE_T0
phase_start() {
  local t; t="$(date +%s.%N)"; PHASE_T0[$1]="$t"
  printf '%s\tstart\t%s\n' "$1" "$t" >> "$PHASE_LOG"
  echo "[PHASE] $1 start"
}
phase_end() {
  local t; t="$(date +%s.%N)"
  printf '%s\tend\t%s\n' "$1" "$t" >> "$PHASE_LOG"
  echo "[PHASE] $1 end ($(awk -v a="${PHASE_T0[$1]}" -v b="$t" 'BEGIN{printf "%.2f", b-a}')s)"
}

# Clean leftovers
phase_start pre_cleanup
sudo mn -c >/dev/null 2>&1 || true
sudo pkill -f "ryu-manager" >/dev/null 2>&1 || true
phase_end pre_cleanup

# Start controller
phase_start controller_start
export OUTDIR="$OUTDIR"
source "$ROOT/venv_py310/bin/activate" 2>/dev/null || source "$ROOT/venv/bin/activate"

ryu-manager "$ROOT/controller/ryu_instrumented_switch.py" \
  >"$OUTDIR/ryu_stdout.log" 2>"$OUTDIR/ryu_stderr.log" &
RYU_PID=$!
echo "$RYU_PID" > "$OUTDIR/ryu_pid.txt"
echo "[INFO] RYU_PID=$RYU_PID"

# Start system metrics logger
PYBIN="$(command -v python3)"
"$PYBIN" "$ROOT/scripts/log_system_metrics.py" \
  --pid "$RYU_PID" --outdir "$OUTDIR" --interval 1.0 \
  >"$OUTDIR/syslog_stdout.log" 2>"$OUTDIR/syslog_stderr.log" &
SYS_PID=$!
echo "$SYS_PID" > "$OUTDIR/syslog_pid.txt"

# Give controller a moment
sleep 1
phase_end controller_start

# Start Mininet + iperf traffic
phase_start mininet_run
cd "$ROOT"
sudo /usr/bin/python3 "$ROOT/scripts/run_mininet_iperf.py" \
  --n "$N" \
  --controller_ip 127.0.0.1 \
  --controller_port "$PORT" \
  --flows_per_sec "$FLOWS_PER_SEC" \
  --flow_duration "$FLOW_DURATION" \
  --total_time "$TOTAL_TIME" \
  --seed "$SEED" \
  --outdir "$OUTDIR" \
  --topo "$TOPO" \
  --host_bw "$HOST_BW" \
  --fq_rate "$FQ_RATE" \
  > "$OUTDIR/mininet_iperf.log" 2>&1 || true
phase_end mininet_run

# Stop background processes
phase_start teardown
kill "$SYS_PID" >/dev/null 2>&1 || true
kill "$RYU_PID" >/dev/null 2>&1 || true
sleep 1
sudo mn -c >/dev/null 2>&1 || true
sudo chown -R "$(id -un)":"$(id -gn)" "$OUTDIR" >/dev/null 2>&1 || true
phase_end teardown

echo "[DONE] Run complete: $RUN_ID"
echo "[DONE] Files in: $OUTDIR"

# ── Inline aggregation ────────────────────────────────────────────────────────
echo "[AGG] Aggregating $RUN_ID ..."
phase_start aggregation
if "$PYBIN" "$ROOT/analysis/aggregate_results.py" \
    --rundir "$OUTDIR" \
    --summary "$SUMMARY"; then
  phase_end aggregation
  phase_start cleanup

  echo "[CLEANUP] Removing bulky raw files for $RUN_ID ..."

  # Delete large raw files — keep the CSVs and meta JSON
  if [ "$KEEP_IPERF_JSON" = "1" ]; then
    echo "[CLEANUP] KEEP_IPERF_JSON=1: keeping $OUTDIR/iperf_json"
  else
    rm -rf "$OUTDIR/iperf_json"
  fi
  rm -f  "$OUTDIR/ryu_stdout.log"
  rm -f  "$OUTDIR/ryu_stderr.log"
  rm -f  "$OUTDIR/mininet_iperf.log"
  rm -f  "$OUTDIR/syslog_stdout.log"
  rm -f  "$OUTDIR/syslog_stderr.log"
  phase_end cleanup

  echo "[CLEANUP] Done. Kept: controller_metrics.csv, flow_events.csv, system_metrics.csv, traffic_meta.json, iperf_summary.json, phase_times.json"
else
  phase_end aggregation
  echo "[WARNING] Aggregation failed for $RUN_ID — raw files kept for manual inspection."
fi

"$PYBIN" "$ROOT/scripts/phase_times.py" --rundir "$OUTDIR" || true

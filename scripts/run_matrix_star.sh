#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOSTS_LIST=(4 8 16 32 64)
FPS_LIST=(25 50 100 200)
REPS=10

TOTAL_TIME=20
FLOW_DURATION=2

for N in "${HOSTS_LIST[@]}"; do
  for FPS in "${FPS_LIST[@]}"; do
    for REP in $(seq 1 "$REPS"); do
      RUN_ID="STAR_N${N}_F${FPS}_R${REP}"
      SEED=$((1000 + N*10 + FPS + REP))

      echo "[RUN] $RUN_ID"
      "$ROOT/scripts/run_experiment.sh" "$N" "$RUN_ID" "$FPS" "$TOTAL_TIME" "$FLOW_DURATION" "$SEED"
    done
  done
done

echo "[DONE] Matrix completed."


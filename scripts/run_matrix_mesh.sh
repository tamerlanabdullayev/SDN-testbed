#!/usr/bin/env bash
set -euo pipefail

# EXPERIMENTAL: the mesh has loops and the reference controller has no STP,
# so runs suffer broadcast storms and are not valid measurements.
echo "[WARN] mesh topology is EXPERIMENTAL (loops, no STP in the controller): results are not valid." >&2

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOSTS_LIST=(4 8 16 32)
FPS_LIST=(25 50 100 200)
REPS=5
TOTAL_TIME=20
FLOW_DURATION=2
TOPO="mesh"

for N in "${HOSTS_LIST[@]}"; do
  for FPS in "${FPS_LIST[@]}"; do
    for REP in $(seq 1 "$REPS"); do
      RUN_ID="MESH_N${N}_F${FPS}_R${REP}"
      SEED=$((3000 + N*10 + FPS + REP))
      echo "[RUN] $RUN_ID"
      "$ROOT/scripts/run_experiment.sh" "$N" "$RUN_ID" "$FPS" "$TOTAL_TIME" "$FLOW_DURATION" "$SEED" "$TOPO"
    done
  done
done

echo "[DONE] Mesh matrix completed."

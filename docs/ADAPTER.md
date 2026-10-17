# Controller adapter contract

This document describes what a controller must provide to be benchmarked by
the testbed, **as the code works today**. Two controllers implement it:

| Controller | App | Run script | Results |
|---|---|---|---|
| Ryu 4.34 | `controller/ryu_instrumented_switch.py` | `scripts/run_experiment.sh` | `results/` |
| OS-Ken | `controller/osken_instrumented_switch.py` | `scripts/run_experiment_osken.sh` | `results_osken/` |

Today each controller has its own copy of the run script. The responsibilities
are split between the **controller app** (Part 1) and the **run script**
(Part 2). Part 3 shows the complete Ryu → OS-Ken port as a worked example.

> **Planned:** a unified `--controller <name>` option backed by a small
> `ControllerAdapter` interface (`start(outdir, port)`, `wait_ready(timeout)`,
> `stop()`, `pid`), registered by name like the topology factory, replacing
> the per-controller run scripts. It is **not implemented yet**; this
> document is the contract that interface will encode.

---

## Part 1 — Controller app responsibilities

### 1.1 Forwarding logic (must be identical across controllers)

OpenFlow 1.3, reactive MAC-learning L2 switch (Ryu `simple_switch_13` logic):

- **On switch connect** (`EventOFPSwitchFeatures`): install a table-miss
  entry — priority 0, empty match, action `OUTPUT:CONTROLLER` with
  `OFPCML_NO_BUFFER`. This FlowMod is **not** counted in `flow_mod_count`.
- **On every Packet-In** (`EventOFPPacketIn`):
  1. Parse the Ethernet header. If there is none, return immediately
     (counted in `packet_in_count`, but no `flow_events.csv` row is written).
  2. Learn `mac_to_port[dpid][eth_src] = in_port`.
  3. If `eth_dst` is known on this switch → `out_port` = learned port;
     otherwise `out_port = OFPP_FLOOD`.
  4. If `out_port` is not FLOOD → install a flow entry:
     - priority **1**
     - match **`in_port`, `eth_src`, `eth_dst`** (exactly these three fields)
     - instruction `APPLY_ACTIONS: OUTPUT:out_port`
     - **no `idle_timeout` and no `hard_timeout`** (both 0 = permanent)
     - if the switch buffered the packet (`buffer_id != OFP_NO_BUFFER`), the
       FlowMod carries the `buffer_id`, which releases the packet; no
       PacketOut is sent and handling ends here.
  5. Otherwise send a PacketOut with `actions=[OUTPUT:out_port]` (the packet
     data is included when unbuffered). Flooding uses this path with no FlowMod.

Consequences that the paper and README rely on: with permanent entries and
static ARP (`autoStaticArp=True` in Mininet), a Packet-In happens only on the
first packet per directed MAC pair per switch; controller load is bounded by
unique pairs, not by flows per second. (`FLOW_IDLE_TIMEOUT` is planned but not
implemented; today timeouts are always 0.)

### 1.2 Timing point (`proc_time_ms`)

- **Start:** `time.perf_counter()` as the first statement of the Packet-In
  handler (handler entry). Time spent waiting in the framework's event queue
  is **excluded**.
- **End:** `time.perf_counter()` immediately after the last `send_msg` of the
  handler returns:
  - buffered + known destination: after the FlowMod;
  - unbuffered + known destination: after the FlowMod **and** the PacketOut;
  - unknown destination (flood): after the flooding PacketOut.
- `send_msg` serializes the message and puts it on the datapath's send queue
  (Ryu `Datapath.send` → `send_q.put`); the socket write happens later in the
  datapath's send loop and is **not** included.
- The CSV write and flush happen *after* the end timestamp, so logging is
  outside the measured interval (though it does run inside the handler).
- Flood events are included in the samples.

### 1.3 Output directory

- Read the output directory from the environment variable **`OUTDIR`**
  (absolute path set by the run script). Fallback when unset:
  `results/raw/devrun`, relative to the working directory.
- Create it if missing (`os.makedirs(..., exist_ok=True)`).
- Open both CSVs in **`"w"` mode** (truncate) at app start, write the header,
  and **flush after every row**. Files are never explicitly closed: the run
  script stops the controller with SIGTERM, so per-row flushing is what makes
  the data complete.

### 1.4 `flow_events.csv` — one row per handled Packet-In

Header (exact order):

```
ts_wall,dpid,src_mac,dst_mac,in_port,proc_time_ms,msg_len
```

| Column | Type | Meaning |
|---|---|---|
| `ts_wall` | float, Unix epoch seconds | `time.time()` taken when the row is written, i.e. *after* handling (not handler entry) |
| `dpid` | int | datapath ID of the switch that sent the Packet-In |
| `src_mac` | string `aa:bb:cc:dd:ee:ff` | Ethernet source |
| `dst_mac` | string `aa:bb:cc:dd:ee:ff` | Ethernet destination (may be broadcast) |
| `in_port` | int | `msg.match['in_port']`, or `-1` if absent |
| `proc_time_ms` | float, milliseconds | handler-entry → last `send_msg` returned (§1.2) |
| `msg_len` | int, bytes | `len(msg.data)` — length of the packet data in the Packet-In |

Used by the aggregator for `proc_latency_p50_ms` and `proc_latency_p95_ms`.

### 1.5 `controller_metrics.csv` — one row per ~1 s

Header (exact order):

```
ts_wall,packet_in_count,flow_mod_count
```

| Column | Type | Meaning |
|---|---|---|
| `ts_wall` | float, Unix epoch seconds | time the row is written |
| `packet_in_count` | int | Packet-Ins received since the previous row (all, including non-Ethernet) |
| `flow_mod_count` | int | priority-1 FlowMods sent since the previous row (table-miss excluded) |

The first row is written at app start and is always `0,0`. Both counters are
reset to 0 right after each row is written. The aggregator reports the mean of
each column over all rows as `avg_packet_in_rate` and `avg_flow_mod_rate`.

### 1.6 Per-second logger (must not block the event loop)

- Runs as a framework green thread: `hub.spawn(self._per_second_logger)` from
  `__init__`.
- Loop: write row → flush → reset counters → `hub.sleep(1.0)`.
- It **must** sleep with the framework's cooperative sleep (`hub.sleep`), never
  `time.sleep`, which would block the eventlet hub and stall Packet-In handling.
- Green threads are cooperative, so reading and resetting the counters cannot
  interleave with a running handler; no lock is needed.
- The period is 1 s *plus* the write time, so rows drift slightly from exact
  1 Hz; `ts_wall` records the actual times.

---

## Part 2 — Run script responsibilities

What `scripts/run_experiment.sh` does for the controller (the rest of the
script — Mininet, traffic, aggregation, phase timing — is controller-independent).

Lifecycle at a glance (these map onto the planned `ControllerAdapter` methods):

| Step | Today (shell) | Planned adapter |
|---|---|---|
| start | launch the manager in the background with `OUTDIR` exported (§2.2) | `start(outdir, port)` |
| pid | `$!` of that launch, saved to `<name>_pid.txt` and given to the monitor (§2.3) | `pid` |
| ready | fixed `sleep 1` (§2.4) | `wait_ready(timeout)` |
| stop | `kill <pid>` (SIGTERM), then `mn -c` (§2.5) | `stop()` |

### 2.1 Pre-cleanup

```bash
sudo mn -c
sudo pkill -f "ryu-manager"          # the controller's own process name
```

### 2.2 Environment and launch

```bash
export OUTDIR="$OUTDIR"              # read by the app (§1.3)
source "$ROOT/venv_py310/bin/activate" 2>/dev/null || source "$ROOT/venv/bin/activate"

ryu-manager "$ROOT/controller/ryu_instrumented_switch.py" \
  >"$OUTDIR/ryu_stdout.log" 2>"$OUTDIR/ryu_stderr.log" &
```

- The controller runs **without sudo**, in the background.
- It listens on the framework's default OpenFlow port, **6653** (Ryu:
  `ofproto_common.OFP_TCP_PORT`). The script's `PORT=6653` is passed only to
  Mininet (`--controller_port`), not to the controller, so the two must agree
  by default.

### 2.3 PID capture for the monitor

```bash
RYU_PID=$!
echo "$RYU_PID" > "$OUTDIR/ryu_pid.txt"
"$PYBIN" "$ROOT/scripts/log_system_metrics.py" --pid "$RYU_PID" --outdir "$OUTDIR" --interval 1.0 &
```

- `$!` must be the **controller process itself** — the launch command must
  not go through a wrapper (`sudo`, `sh -c`, a launcher that forks), because
  `log_system_metrics.py` samples exactly that PID with psutil (children are
  not included).
- The monitor writes `system_metrics.csv`:
  `ts_wall,cpu_percent,rss_bytes,vms_bytes,num_threads` (CPU % of one core,
  so it can exceed 100 for multi-threaded controllers; bytes for memory). It
  exits by itself when the PID disappears.
- The monitor primes psutil and then sleeps one interval before each sample,
  so every sample covers a full interval. The published `summary_v2.csv` was
  produced by the earlier version, which sampled immediately after priming;
  its first sample per run is an artifact (0 or thousands of %), and the paper's
  CPU figures drop it (`analysis/recompute_cpu.py` → `cpu_recomputed.csv`).

### 2.4 Readiness

There is **no readiness check today**: after starting the controller and the
monitor the script does `sleep 1`, then starts Mininet. Switches retry the
controller connection, so a slow start delays the first Packet-Ins rather
than failing the run. (`wait_ready(timeout)` in the planned adapter is meant to
replace this, e.g. by polling for a listener on the OpenFlow port.)

### 2.5 Teardown

```bash
kill "$SYS_PID"                      # monitor
kill "$RYU_PID"                      # SIGTERM to the controller
sleep 1
sudo mn -c
sudo chown -R "$(id -un)":"$(id -gn)" "$OUTDIR"
```

### 2.6 Output paths

| Item | Ryu | OS-Ken |
|---|---|---|
| run directory (`OUTDIR`) | `results/raw/<RUN_ID>/` | `results_osken/raw/<RUN_ID>/` |
| summary | `results/summary/summary_v2.csv` | `results_osken/summary/summary_v2.csv` |
| controller logs | `ryu_stdout.log`, `ryu_stderr.log` | `osken_stdout.log`, `osken_stderr.log` |
| PID file | `ryu_pid.txt` | `osken_pid.txt` |

Files kept in the run directory after a successful aggregation:
`flow_events.csv`, `controller_metrics.csv` (from the app),
`system_metrics.csv` (monitor), `traffic_meta.json`, `iperf_summary.json`,
`phase_times.json`, and the PID files. The controller logs, `iperf_json/` and
the other `*.log` files are deleted on success and kept if aggregation fails.

### 2.7 Environment notes (current machine)

- `ryu-manager` resolves through `venv_py310/bin/activate`. This venv was
  copied from the frozen snapshot, and its `activate` hard-codes
  `VIRTUAL_ENV=/home/tamerlan/Desktop/sdn-paper1/venv_py310`, so Ryu (and the
  `python3` used for the monitor and aggregator) actually come from the
  snapshot's venv.
- `osken-manager` is not in that venv; it resolves to the system package
  `/usr/bin/osken-manager` (`#!/usr/bin/python3`), i.e. OS-Ken 2.8.1 on
  **Python 3.12.3** with eventlet 0.35.2, while Ryu runs on Python 3.10.19 with
  eventlet 0.33.3. Controller comparisons are therefore confounded by the
  interpreter version; see `docs/ENVIRONMENT.md`.

---

## Part 3 — Worked example: porting Ryu → OS-Ken

OS-Ken is a fork of Ryu, so the port is purely mechanical. These are the
**complete** differences today.

### 3.1 Controller app (`ryu_instrumented_switch.py` → `osken_instrumented_switch.py`)

Imports and base class only; logic, timing points, CSV schemas and `OUTDIR`
handling are byte-identical.

```diff
-from ryu.base import app_manager
-from ryu.controller import ofp_event
-from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
-from ryu.controller.handler import set_ev_cls
-from ryu.ofproto import ofproto_v1_3
-from ryu.lib.packet import packet, ethernet
-from ryu.lib import hub
+from os_ken.base import app_manager
+from os_ken.controller import ofp_event
+from os_ken.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
+from os_ken.controller.handler import set_ev_cls
+from os_ken.ofproto import ofproto_v1_3
+from os_ken.lib.packet import packet, ethernet
+from os_ken.lib import hub

-class InstrumentedSimpleSwitch13(app_manager.RyuApp):
+class InstrumentedSimpleSwitch13(app_manager.OSKenApp):
```

### 3.2 Run script (`run_experiment.sh` → `run_experiment_osken.sh`)

```diff
-OUTDIR="$ROOT/results/raw/$RUN_ID"
-SUMMARY="$ROOT/results/summary/summary_v2.csv"
+OUTDIR="$ROOT/results_osken/raw/$RUN_ID"
+SUMMARY="$ROOT/results_osken/summary/summary_v2.csv"
 mkdir -p "$OUTDIR"
+mkdir -p "$ROOT/results_osken/summary"

-sudo pkill -f "ryu-manager" >/dev/null 2>&1 || true
+sudo pkill -f "osken-manager" >/dev/null 2>&1 || true

-ryu-manager "$ROOT/controller/ryu_instrumented_switch.py" \
-  >"$OUTDIR/ryu_stdout.log" 2>"$OUTDIR/ryu_stderr.log" &
-RYU_PID=$!
-echo "$RYU_PID" > "$OUTDIR/ryu_pid.txt"
-echo "[INFO] RYU_PID=$RYU_PID"
+osken-manager "$ROOT/controller/osken_instrumented_switch.py" \
+  >"$OUTDIR/osken_stdout.log" 2>"$OUTDIR/osken_stderr.log" &
+OSKEN_PID=$!
+echo "$OSKEN_PID" > "$OUTDIR/osken_pid.txt"
+echo "[INFO] OSKEN_PID=$OSKEN_PID"

-  --pid "$RYU_PID" --outdir "$OUTDIR" --interval 1.0 \
+  --pid "$OSKEN_PID" --outdir "$OUTDIR" --interval 1.0 \

-kill "$RYU_PID" >/dev/null 2>&1 || true
+kill "$OSKEN_PID" >/dev/null 2>&1 || true

-  rm -f  "$OUTDIR/ryu_stdout.log"
-  rm -f  "$OUTDIR/ryu_stderr.log"
+  rm -f  "$OUTDIR/osken_stdout.log"
+  rm -f  "$OUTDIR/osken_stderr.log"
```

(The OS-Ken copy also lacks one comment line in the cleanup block; no
functional difference.)

### 3.3 Matrix scripts

`run_matrix_osken_star.sh` / `run_matrix_osken_fattree.sh` differ from the Ryu
ones in the run-ID prefix (`OSKEN_STAR_…`, `OSKEN_FATTREE_…`), in calling
`run_experiment_osken.sh`, and in how the topology is passed: the OS-Ken
scripts pass `"star"` / `"fattree"` literally, while `run_matrix_fattree.sh`
uses `TOPO="fattree"` and `run_matrix_star.sh` passes nothing (the
orchestrator's default is `star`). Seeds follow the same formula (`B_T + 10N + FPS +
rep`, `B_T` = 1000 star, 2000 tree), so both controllers see identical
src/dst flow sequences.

### 3.4 Checklist for adding another controller today

1. Port the app so §1.1–§1.6 hold exactly (same forwarding logic, timing
   points, CSV schemas, `OUTDIR`, cooperative logger).
2. Copy `run_experiment.sh`; change the results root, the `pkill` pattern, the
   launch command and log/PID names (§3.2). Make sure `$!` is the controller
   process itself and that it listens on 6653.
3. Copy a matrix script; change the run-ID prefix and the orchestrator it calls.
4. Smoke test one run and check that `flow_events.csv`,
   `controller_metrics.csv` and `system_metrics.csv` are non-empty.

# Measurement environment

Software versions used for the published sweep (800 runs: Ryu and OS-Ken,
star and two-tier tree), recorded on 2026-10-16 from the measurement machine.

## Host

| Component | Version |
|---|---|
| OS | Ubuntu 24.04.2 LTS |
| Kernel | 7.0.0-38-generic |
| Open vSwitch | 3.3.4 (apt `openvswitch-switch` 3.3.4-0ubuntu0.24.04.1) |
| Mininet | 2.3.0 (apt `mininet` 2.3.0-1.1), run with system `/usr/bin/python3` under sudo |
| iperf3 | 3.16 (apt `iperf3` 3.16-1build2, cJSON 1.7.15) |

## Controllers: the two ran on different interpreters

| | Ryu runs | OS-Ken runs |
|---|---|---|
| Launcher | `ryu-manager` from virtualenv `venv_py310` | `/usr/bin/osken-manager` (apt `python3-os-ken`) |
| Python | **3.10.19** | **3.12.3** (system) |
| Framework | ryu 4.34 (source install, patched; see README) | os-ken **2.8.1** |
| eventlet | 0.33.3 | 0.35.2 (apt `python3-eventlet` 0.35.2-0ubuntu1.1) |
| greenlet | 3.3.1 | 3.0.3 |
| dnspython | 2.1.0 | 2.6.1 |
| msgpack | 1.1.2 | 1.0.3 |
| oslo.config | 10.2.0 | 9.4.0 |
| netaddr | 1.3.0 | 0.8.0 |
| ovs (Python bindings) | 3.6.1 | 3.3.4 |
| tinyrpc | 1.1.7 | 0.6 |
| webob / routes / six | 1.8.9 / 2.5.1 / 1.17.0 | 1.8.7 / 2.5.1 / 1.16.0 |

**Interpretation.** OS-Ken measured ~17% lower p50 handler time than Ryu. The
two controllers share the same application logic (see `docs/ADAPTER.md`), but
the comparison is confounded: OS-Ken ran on CPython 3.12, which is generally
faster than 3.10 for pure-Python code like this handler, and with a newer
eventlet. The difference should not be attributed to the frameworks alone.

Notes:
- `venv_py310` also contains os-ken 4.2.2, but it was **not** used: the
  virtualenv has no `osken-manager` entry point, so `PATH` resolves to the
  system `/usr/bin/osken-manager` (`#!/usr/bin/python3`), which imports
  `/usr/lib/python3/dist-packages/os_ken`. This is inferred from `PATH`
  resolution on the measurement machine; per-run controller logs were deleted by
  the cleanup step.
- On the measurement machine the working copy's `venv_py310/bin/activate`
  points to the original checkout's virtualenv
  (`/home/tamerlan/Desktop/sdn-paper1/venv_py310`); its package set is identical
  to the table above.

## Monitoring and analysis (virtualenv Python 3.10.19)

| Package | Version | Used by |
|---|---|---|
| psutil | 7.2.2 | `scripts/log_system_metrics.py` (both controllers) |
| pandas | 2.3.3 | `analysis/plots_v4.py` |
| numpy | 2.2.6 | `analysis/plots_v4.py` |
| matplotlib | 3.10.8 | `analysis/plots_v4.py` |

The system Python has no psutil; the run scripts call the monitor and the
aggregator with the virtualenv's `python3` (`PYBIN` after activation).

## Reproducing this listing

```bash
# Controller / monitoring virtualenv
venv_py310/bin/python3 -c 'import sys, importlib.metadata as m; print(sys.version)
for p in ("ryu","os-ken","eventlet","greenlet","psutil","pandas","numpy","matplotlib"):
    print(p, m.version(p))'

# System Python (OS-Ken, Mininet)
/usr/bin/python3 -c 'import sys, importlib.metadata as m; print(sys.version)
for p in ("os-ken","eventlet","greenlet","mininet"):
    print(p, m.version(p))'
head -1 "$(command -v osken-manager)"

# System tools
ovs-vsctl --version | head -1
iperf3 --version | head -1
dpkg -l mininet openvswitch-switch iperf3 python3-os-ken python3-eventlet | awk '/^ii/{print $2, $3}'
uname -r; lsb_release -ds
```

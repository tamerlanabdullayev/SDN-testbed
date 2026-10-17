"""
iperf_pool.py
iperf3 server-pool sizing and per-destination port assignment.

An `iperf3 -s` process serves one test at a time; a client that connects
while it is busy is rejected ("the server is busy running a test").  Each
host therefore runs a pool of k servers on distinct ports, and every flow is
assigned the least-recently-used port on its destination host.

Kept free of Mininet imports so it can be unit-tested without sudo.
"""

import math

PORT_BASE = 5000


def pool_size(flows_per_sec, hold_s, n_hosts,
              safety=1.5, z=3.0, min_servers=4, cap=320):
    """
    Servers per host.

    hold_s is how long a port stays occupied per flow (flow_duration plus a
    guard for iperf3 setup/teardown; PortPool must use the same value).
    Mean concurrently held ports per destination is lambda = FPS * hold_s / N
    (destinations are uniform).  The count of concurrent flows is roughly
    Poisson(lambda), so a pure multiplicative margin (lambda * safety) is too
    thin when lambda is small; add z standard deviations (sqrt(lambda)).

        k = clamp(ceil(lambda * safety + z * sqrt(lambda)), min_servers, cap)
    """
    lam = flows_per_sec * hold_s / float(n_hosts)
    k = math.ceil(lam * safety + z * math.sqrt(lam))
    return max(min_servers, min(cap, k))


class PortPool:
    """
    Least-recently-used port assignment, per destination host.

    Uses no randomness, so the seeded src/dst sequence is unaffected.
    A port is considered busy for `busy_s` seconds after a flow is assigned
    to it (flow_duration plus a guard for iperf3 setup/teardown).  If every
    port on the destination is still busy, the oldest one is used anyway and
    the event is counted in `collisions`.
    """

    def __init__(self, n_hosts, servers_per_host, busy_s, base=PORT_BASE):
        self.n_hosts = n_hosts
        self.k = servers_per_host
        self.busy_s = busy_s
        self.base = base
        last_port = self.port(n_hosts - 1, self.k - 1)
        if last_port > 65535:
            raise ValueError(f"port range overflow: {last_port} > 65535")
        self._last = [[float("-inf")] * self.k for _ in range(n_hosts)]
        self.collisions = 0

    def port(self, host_idx, j):
        return self.base + host_idx * self.k + j

    def host_ports(self, host_idx):
        return [self.port(host_idx, j) for j in range(self.k)]

    def acquire(self, host_idx, now):
        last = self._last[host_idx]
        j = min(range(self.k), key=last.__getitem__)  # oldest; ties -> lowest j
        if now - last[j] < self.busy_s:
            self.collisions += 1
        last[j] = now
        return self.port(host_idx, j)

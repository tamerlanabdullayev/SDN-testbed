from mininet.topo import Topo


class MeshTopo(Topo):
    """
    EXPERIMENTAL — not part of the published results.

    The switch graph has loops and the reference controller app has no STP or
    other loop prevention, so flooded broadcasts (e.g. ARP) circulate forever
    (broadcast storm). Runs on this topology do not produce valid measurements
    until a loop-preventing controller is used.

    Mesh topology — every switch connects to every other switch.

    Structure:
    - N hosts, each connected to its own switch
    - Every switch is connected to every other switch (full mesh)
    - Simulates a highly connected core network

    For n=4:  4 switches, 4 hosts, 6 inter-switch links
    For n=8:  8 switches, 8 hosts, 28 inter-switch links
    For n=16: 16 switches, 16 hosts, 120 inter-switch links
    For n=32: 32 switches, 32 hosts, 496 inter-switch links

    Note: n=32 mesh is large — Mininet handles it but may be slower to start.
    """

    def build(self, n=4):
        switches = []

        # Add one switch per host
        for i in range(1, n + 1):
            s = self.addSwitch(f's{i}')
            switches.append(s)

        # Add hosts, one per switch
        for i, s in enumerate(switches, start=1):
            h = self.addHost(f'h{i}')
            self.addLink(h, s)

        # Full mesh: connect every switch to every other switch
        for i in range(len(switches)):
            for j in range(i + 1, len(switches)):
                self.addLink(switches[i], switches[j])


topos = {'meshtopo': (lambda n=4: MeshTopo(n=n))}

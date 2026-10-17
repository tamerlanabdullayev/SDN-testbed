from mininet.topo import Topo

class StarTopo(Topo):
    def build(self, n=4):
        s1 = self.addSwitch('s1')
        for i in range(1, n + 1):
            h = self.addHost(f'h{i}')
            self.addLink(h, s1)

topos = {'startopo': (lambda n=4: StarTopo(n=n))}


from mininet.topo import Topo

class TSNRouterTopo(Topo):
    def build(self):
        # hosts
        h1 = self.addHost('h1')  # priority sender
        h3 = self.addHost('h3')  # background sender
        h2 = self.addHost('h2')  # receiver

        # r1 is just a host node in the topo, we will enable L3 forwarding with another script
        r1 = self.addHost('r1')

        # links: left side has two senders into r1, right side is receiver
        self.addLink(h1, r1, bw=1000)
        self.addLink(h3, r1, bw=1000)
        self.addLink(r1, h2, bw=10)

topos = { 'tsnrouter': (lambda: TSNRouterTopo()) }

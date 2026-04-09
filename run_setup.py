#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import time

from topology import TSNRouterTopo

# addressing
H1_IP = "10.0.1.10/24"
H3_IP = "10.0.3.10/24"
H2_IP = "10.0.2.10/24"

R1_LEFT1_IP = "10.0.1.1/24"  # r1 h1
R1_LEFT2_IP = "10.0.3.1/24"  # r1 h3
R1_RIGHT_IP = "10.0.2.1/24"  # r1 h2

# switch between different modes
MODE = "taprio"  # none | htb | taprio

ENABLE_PTP = True
PTP_OFFSET_US = 500  # offset to test sensitivity of time aware behavior

# traffic settings
H1_RATE = "900K"   # priority flow (keep under 1Mbit class rate)
H3_RATE = "9.2M"     # background flow
DURATION = 15

H1_PORT = 5001
H3_PORT = 5002

BOTTLENECK_IF = "r1-eth2"  # r1 -> h2

def main():
    net = Mininet(
        topo=TSNRouterTopo(),
        controller=None,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True
    )
    net.start()

    h1, h2, h3, r1 = net.get("h1", "h2", "h3", "r1")

    # interface aliases
    r1_if_h1 = "r1-eth0"
    r1_if_h3 = "r1-eth1"
    r1_if_h2 = "r1-eth2"

    # apply static addressing
    for node, iface in [
        (h1, "h1-eth0"),
        (h3, "h3-eth0"),
        (h2, "h2-eth0"),
        (r1, r1_if_h1),
        (r1, r1_if_h3),
        (r1, r1_if_h2),
    ]:
        node.cmd(f"ip addr flush dev {iface}")
        node.cmd(f"ip link set {iface} up")

    h1.cmd(f"ip addr add {H1_IP} dev h1-eth0")
    h3.cmd(f"ip addr add {H3_IP} dev h3-eth0")
    h2.cmd(f"ip addr add {H2_IP} dev h2-eth0")

    r1.cmd(f"ip addr add {R1_LEFT1_IP} dev {r1_if_h1}")
    r1.cmd(f"ip addr add {R1_LEFT2_IP} dev {r1_if_h3}")
    r1.cmd(f"ip addr add {R1_RIGHT_IP} dev {r1_if_h2}")

    # enable L3 forwarding on r1
    r1.cmd("sysctl -w net.ipv4.ip_forward=1")

    # default routes for hosts
    h1.cmd("ip route del default 2>/dev/null || true")
    h1.cmd("ip route add default via 10.0.1.1")

    h3.cmd("ip route del default 2>/dev/null || true")
    h3.cmd("ip route add default via 10.0.3.1")

    h2.cmd("ip route del default 2>/dev/null || true")
    h2.cmd("ip route add default via 10.0.2.1")

    if ENABLE_PTP:
        r1.cmd("ptp4l -i r1-eth0 -i r1-eth1 -i r1-eth2 -m -S > logs/r1_ptp4l.log 2>&1 &")
        h1.cmd("ptp4l -i h1-eth0 -s -m -S > logs/h1_ptp4l.log 2>&1 &")
        h2.cmd("ptp4l -i h2-eth0 -s -m -S > logs/h2_ptp4l.log 2>&1 &")
        h3.cmd("ptp4l -i h3-eth0 -s -m -S > logs/h3_ptp4l.log 2>&1 &")
        time.sleep(2)

    if MODE == "htb":
        r1.cmd(f"tc qdisc del dev {BOTTLENECK_IF} root 2>/dev/null || true")

        # parent and default goes to background class 1:30
        r1.cmd(f"tc qdisc add dev {BOTTLENECK_IF} root handle 1: htb default 30")
        r1.cmd(f"tc class add dev {BOTTLENECK_IF} parent 1: classid 1:1 htb "
               f"rate 10mbit ceil 10mbit burst 15k cburst 15k")

        # priority h1 and background h3 classes
        r1.cmd(f"tc class add dev {BOTTLENECK_IF} parent 1:1 classid 1:10 htb "
               f"rate 1mbit ceil 10mbit prio 0 burst 15k cburst 15k")
        r1.cmd(f"tc class add dev {BOTTLENECK_IF} parent 1:1 classid 1:30 htb "
               f"rate 9mbit ceil 10mbit prio 7 burst 15k cburst 15k")


        r1.cmd(f"tc qdisc add dev {BOTTLENECK_IF} parent 1:10 handle 10: fq_codel")
        r1.cmd(f"tc qdisc add dev {BOTTLENECK_IF} parent 1:30 handle 30: fq_codel")

        # matching by UDP dest port
        r1.cmd(f"tc filter add dev {BOTTLENECK_IF} protocol ip parent 1: prio 1 u32 "
               f"match ip protocol 17 0xff "
               f"match ip dport {H1_PORT} 0xffff "
               f"flowid 1:10")
        r1.cmd(f"tc filter add dev {BOTTLENECK_IF} protocol ip parent 1: prio 2 u32 "
               f"match ip protocol 17 0xff "
               f"match ip dport {H3_PORT} 0xffff "
               f"flowid 1:30")
        
    elif MODE == "taprio":
        r1.cmd(f"tc qdisc del dev {BOTTLENECK_IF} root 2>/dev/null || true")
        
        # calculate base time in the future and apply offset
        current_time = int(time.clock_gettime(time.CLOCK_TAI) * 1e9) # 1e9 convert to nanoseconds
        base_time_ns = current_time + int(2_000_000_000) + int(PTP_OFFSET_US * 1000) # add 2 seconds to the current time to ensure by the base time isnt in the past by the time tc qdisc is executed

        # 2 traffic classes
        # map priority 1 to TC 0, and default (0) to TC 1
        r1.cmd(f"tc qdisc add dev {BOTTLENECK_IF} parent root handle 100: taprio "
               f"num_tc 2 "                 # one for priority, one for background
               f"map 1 0 1 1 1 1 1 1 1 1 1 1 1 1 1 1 "  # all except 2nd are set to background traffic 
               f"queues 1@0 1@1 "
               f"base-time {base_time_ns} "
               f"sched-entry S 01 500000 "  # TC 0 (Priority H1) transmit for 500us
               f"sched-entry S 02 500000 "  # TC 1 (Background H3) transmit for 500us
               f"clockid CLOCK_TAI")

        # classify H1 priority traffic to priority 1 (maps to TC 0)
        r1.cmd(f"tc filter add dev {BOTTLENECK_IF} parent 100: protocol ip prio 1 u32 "
               f"match ip protocol 17 0xff "
               f"match ip dport {H1_PORT} 0xffff "
               f"action skbedit priority 1")
        
        # classify H3 background traffic to priority 0 (maps to TC 1)
        r1.cmd(f"tc filter add dev {BOTTLENECK_IF} parent 100: protocol ip prio 2 u32 "
               f"match ip protocol 17 0xff "
               f"match ip dport {H3_PORT} 0xffff "
               f"action skbedit priority 0")




    # capture traffic
    r1.cmd(f"tcpdump -w logs/traffic_capture.pcap -i {BOTTLENECK_IF} &")

    # two UDP servers running on 2 different ports
    h2.cmd(f"iperf -s -u -p {H1_PORT} -w 4M > logs/h2_server_h1.log 2>&1 &")
    h2.cmd(f"iperf -s -u -p {H3_PORT} -w 4M > logs/h2_server_h3.log 2>&1 &")

    # clients
    h1.cmd(f"iperf -c 10.0.2.10 -u -p {H1_PORT} -w 4M -b {H1_RATE} -t {DURATION} "
           f"> logs/h1_client.log 2>&1 &")
    h3.cmd(f"iperf -c 10.0.2.10 -u -p {H3_PORT} -w 4M -b {H3_RATE} -t {DURATION} "
           f"> logs/h3_client.log 2>&1 &")

    # wait for traffic to finish, then stop tcpdump
    time.sleep(DURATION + 2)
    r1.cmd("pkill tcpdump")
    if ENABLE_PTP:
        h1.cmd("pkill ptp4l")
        h2.cmd("pkill ptp4l")
        h3.cmd("pkill ptp4l")
        r1.cmd("pkill ptp4l")


    CLI(net)
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    main()

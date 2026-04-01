#!/usr/bin/env python3
"""
SDN-FRL Mesh Topology Script
Sets up a Mininet-WiFi mesh network with mobility and wmediumd for realistic interference.
Connects to a Remote Ryu Controller for SD-QoS routing.
"""

from mn_wifi.net import Mininet_wifi
from mn_wifi.node import Station, OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wlan
from mn_wifi.wmediumdConnector import wmediumd_allow_all
from mininet.node import RemoteController
from mininet.log import setLogLevel, info
import sys
import os

def topology():
    # Use RemoteController (Ryu)
    net = Mininet_wifi(controller=RemoteController, link=wlan, wmediumd_mode=wmediumd_allow_all)

    info("*** Creating nodes\n")
    # Mobile Stations (Mesh Nodes)
    sta1 = net.addStation('sta1', mac='00:00:00:00:00:01', ip='10.0.0.1/8', position='10,10,0')
    sta2 = net.addStation('sta2', mac='00:00:00:00:00:02', ip='10.0.0.2/8', position='20,20,0')
    sta3 = net.addStation('sta3', mac='00:00:00:00:00:03', ip='10.0.0.3/8', position='30,30,0')
    sta4 = net.addStation('sta4', mac='00:00:00:00:00:04', ip='10.0.0.4/8', position='40,40,0')

    # Access Point acting as a Mesh Gateway/Bridge
    ap1 = net.addAccessPoint('ap1', ssid='mesh-pay', mode='g', channel='1', position='25,25,0')
    
    info("*** Configuring Remote Controller (Ryu)\n")
    c1 = net.addController('c1', controller=RemoteController, ip='127.0.0.1', port=6633)

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model='logDistance', exp=3)

    info("*** Configuring wifi nodes\n")
    net.configureWifiNodes()

    info("*** Starting network\n")
    net.build()
    c1.start()
    ap1.start([c1])

    info("*** Setting Mobility Model (RandomWalk)\n")
    net.setMobilityModel(time=0, model='RandomWalk', max_x=100, max_y=100, seed=42)

    info("*** Starting FRL Clients and Performance Probes\n")
    # These will be started once the respective scripts are implemented
    # for sta in [sta1, sta2, sta3, sta4]:
    #     sta.cmd('python3 frl_client.py --node %s &' % sta.name)

    info("*** Running CLI\n")
    CLI(net)

    info("*** Stopping network\n")
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    # Ensure mac80211_hwsim is loaded (usually requires sudo)
    # os.system('modprobe mac80211_hwsim')
    topology()

#!/usr/bin/env python3

from mininet.log import setLogLevel, info
from mn_wifi.cli import CLI
from mn_wifi.net import Mininet_wifi
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
import os
import time

def topology():
    "Create TEMPO Mininet-WiFi topology."

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating nodes\n")
    # APs are our Controllers (isolated from each other)
    # Using range=20 to ensure they don't overlap (distance between them is ~113m)
    ap1 = net.addAccessPoint('ap1', ssid='tempo-ssid1', mode='g', channel='1', position='10,10,0', range=30)
    ap2 = net.addAccessPoint('ap2', ssid='tempo-ssid2', mode='g', channel='6', position='90,90,0', range=30)

    # Stations are our Data Mules
    stas = []
    for i in range(1, 11):
        if i == 1:
            # Special Data Mule (sta1) ferries data between ap1 and ap2
            sta = net.addStation(f'sta{i}', mac=f'00:00:00:00:00:{i:02x}', range=20)
        else:
            sta = net.addStation(f'sta{i}', mac=f'00:00:00:00:00:{i:02x}', range=20)
        stas.append(sta)

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model="logDistance", exp=3.0)

    info("*** Configuring wifi nodes\n")
    net.configureWifiNodes()

    info("*** Plotting Graph\n")
    net.plotGraph(max_x=100, max_y=100)

    info("*** Setting Mobility\n")
    # Setting mobility model for Random STAs (sta2 - sta10)
    net.setMobilityModel(time=0, model='RandomDirection', max_x=100, max_y=100, min_v=0.5, max_v=2.0, seed=20)
    
    # Setting specific trajectory for Data Mule (sta1)
    # Starts at AP1, moves to AP2, then back
    net.startMobility(time=0, mob_rep=2) # 2 repetitions
    net.mobility(stas[0], 'start', time=1, position='10,10,0')
    net.mobility(stas[0], 'step', time=30, position='90,90,0')
    net.mobility(stas[0], 'step', time=60, position='10,10,0')
    net.stopMobility(time=61)

    info("*** Starting network\n")
    net.build()
    
    ap1.start([])
    ap2.start([])

    # Let Mininet-WiFi assign default IPs then we start our apps
    # The default IP subnet for APs in Mininet-WiFi depends on bridge/NAT, 
    # but we can force static IPs to ensure Mule apps know where to send.
    ap1.setIP('10.0.0.101/8')
    ap2.setIP('10.0.0.102/8')

    # Give stas IPs in the same subnet so they can UDP to APs when connected
    for i, sta in enumerate(stas):
        sta.setIP(f'10.0.0.{i+1}/8')

    info("*** Starting Controller and Mule Applications\n")
    
    script_dir = "/home/huydq/PHD2024-2027/meshpay/sdn_frl"
    
    # Start controllers
    # Running in background and redirecting output
    ap1.cmd(f"python3 {script_dir}/controller_app.py --node ap1 --port 9000 > /tmp/ap1_tempo.log 2>&1 &")
    ap2.cmd(f"python3 {script_dir}/controller_app.py --node ap2 --port 9000 > /tmp/ap2_tempo.log 2>&1 &")
    
    # Start mules
    ap_ips = "10.0.0.101,10.0.0.102"
    for sta in stas:
        sta.cmd(f"python3 {script_dir}/mule_app.py --node {sta.name} --ap-ips {ap_ips} > /tmp/{sta.name}_tempo.log 2>&1 &")

    info("*** Running CLI\n")
    info("To view logs, exit CLI or open another terminal and try 'cat /tmp/ap1_tempo.log'\n")
    CLI(net)

    info("*** Stopping network\n")
    # Kill the background python processes
    for node in [ap1, ap2] + stas:
        node.cmd("kill -9 $(pgrep -f 'python3 .*_app.py')")
        
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    topology()

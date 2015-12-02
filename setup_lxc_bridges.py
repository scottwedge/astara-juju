#!/usr/bin/python

import yaml
import subprocess
import sys

status = yaml.load(subprocess.check_output(['juju', 'status']))
machines = status['machines']
machine_ids = sorted(machines.keys())[1:]


NET_INT = """
auto lxcbr0
 iface lxcbr0 inet dhcp
 bridge_ports eth0
"""

addrs = []
for mid in machine_ids:
    if not machines[mid].get('dns-name'):
        print 'not all machines have dns-name'
        sys.exit(1)
    if not machines[mid].get('agent-state') == 'started':
        print 'not all machines have started agents'
        sys.exit(1)


for mid in machine_ids:
    print 'configuring machine %s' % mid
    cmd = [
        'juju', 'ssh', mid,
        'sudo rm -rf /etc/network/interfaces.d/eth0.cfg']
    subprocess.check_call(cmd)
    cmd = [
        'juju', 'ssh', mid,
        "echo '%s' | sudo tee /etc/network/interfaces.d/lxcbr0.cfg" % NET_INT
    ]
    subprocess.check_call(cmd)

    cmd = [
        'juju', 'ssh', mid,
        'sudo ifdown eth0 && sudo ifup lxcbr0'
    ]
    subprocess.check_call(cmd)

#!/usr/bin/python

import yaml
import subprocess
import sys
import time

READY_TIMEOUT = 340


def status():
    return yaml.load(subprocess.check_output(['juju', 'status']))


try:
    subprocess.check_call(['juju', 'bootstrap', '-e', 'local'])
except:
    pass

while True:
    print 'waiting for bootstrap'
    st = status()
    bootstrap_machine = st.get('machines')['0']
    if bootstrap_machine.get('agent-state') == 'started':
        print 'bootstrap done'
        break
    time.sleep(1)


if len(status().get('machines').keys()) == 1:
    print 'adding machines'
    for machine in ['4G', '2G']:
        subprocess.check_call([
            'juju',
            'add-machine',
            '--constraints',
            'mem=%s cpu-cores=2 root-disk=25G' % machine])
else:
    print 'using existing machines'


timeout = 0
while True:
    st = status()
    ready = True
    for mid, m in status()['machines'].iteritems():
        if m.get('agent-state') != 'started':
            ready = False
        if not m.get('dns-name'):
            ready = False
        if ready:
            print 'all machines ready after %s sec' % timeout
            sys.exit(0)
        if timeout == READY_TIMEOUT:
            print 'timed out waiting for machines'
            sys.exit(1)
        print 'waiting on ready machines'
        time.sleep(1)
        timeout += 1

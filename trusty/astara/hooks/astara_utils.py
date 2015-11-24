# Copyright (c) 2015 Akanda, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import json
import netaddr
import os
import subprocess

from collections import OrderedDict

from charmhelpers.contrib.openstack import context, templating

from neutronclient.v2_0 import client as NeutronClient

import astara_context

from charmhelpers.contrib.openstack.utils import (
    git_install_requested,
)


from charmhelpers.core.hookenv import (
    charm_dir,
    config,
    log as juju_log
)

from charmhelpers.core.host import (
    adduser,
    add_group,
    add_user_to_group,
    mkdir,
    service_stop,
    service_start,
    service_restart,
    write_file,
)


from charmhelpers.contrib.openstack.utils import (
    git_install_requested,
    git_clone_and_install,
    git_src_dir,
    git_yaml_value,
    git_pip_venv_dir,
)


from charmhelpers.contrib.python.packages import pip_install

from charmhelpers.core.templating import render

TEMPLATES = 'templates/'
ASTARA_CONFIG = '/etc/astara/orchestrator.ini'
ASTARA_NETWORK_CACHE = '/etc/astara/astara_net_cache.juju'

CONSOLE_SCRIPTS = [
    'astara-ctl',
    'astara-dbsync',
    'astara-debug-router',
    'astara-orchestrator',
    'astara-pez-service',
]

PACKAGES = [
    'python-glanceclient',
    'python-neutronclient',
]

BASE_GIT_PACKAGES = [
    'libffi-dev',
    'libmysqlclient-dev',
    'libxml2-dev',
    'libxslt1-dev',
    'libssl-dev',
    'libyaml-dev',
    'python-dev',
    'python-pip',
    'python-setuptools',
    'zlib1g-dev',
]


def validate_config():
    mgt_net = config('management-network-cidr')
    try:
        net = netaddr.IPNetwork(mgt_net)
    except netaddr.core.AddrFormatError as e:
        m = (
            'Invalid network CIDR configured for management-network-cidr: %s'
             % mgt_net
        )
        juju_log(m)
        raise  Exception(m)


def determine_packages():
    packages = set(PACKAGES)
    if git_install_requested():
        packages |= set(BASE_GIT_PACKAGES)
    return packages


def resource_map():
    rm = OrderedDict([
        (ASTARA_CONFIG, {
            'services': ['astara-orchestrator'],
            'contexts': [
                context.AMQPContext(),
                context.SharedDBContext(),
                context.IdentityServiceContext(
                    service='astara',
                    service_user='astara'),
                astara_context.AstaraContext(
                    network_cache=ASTARA_NETWORK_CACHE,
                )
            ],
        })
    ])
    return rm

def restart_map():
    return OrderedDict([(cfg, v['services'])
                        for cfg, v in resource_map().iteritems()])


def register_configs(release=None):
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release='liberty')
    for cfg, rscs in resource_map().iteritems():
        configs.register(cfg, rscs['contexts'])
    return configs



def git_install(projects_yaml):
    """Perform setup, and install git repos specified in yaml parameter."""
    if git_install_requested():
        git_pre_install()
        git_clone_and_install(projects_yaml, core_project='astara')
        git_post_install(projects_yaml)


def git_pre_install():
    """Perform glance pre-install setup."""
    dirs = [
        '/var/lib/astara',
        '/var/log/astara',
        '/etc/astara',
    ]

    logs = [
        '/var/log/astara/astara-orchestrator.log',
    ]

    adduser('astara', shell='/bin/bash', system_user=True)
    add_group('astara', system_group=True)
    add_user_to_group('astara', 'astara')

    for d in dirs:
        mkdir(d, owner='astara', group='astara', perms=0755, force=False)

    for l in logs:
        write_file(l, '', owner='astara', group='astara', perms=0600)


def git_post_install(projects_yaml):
    """Perform glance post-install setup."""
    http_proxy = git_yaml_value(projects_yaml, 'http_proxy')
    if http_proxy:
        pip_install('mysql-python', proxy=http_proxy,
                    venv=git_pip_venv_dir(projects_yaml))
    else:
        pip_install('mysql-python',
                    venv=git_pip_venv_dir(projects_yaml))

    for cs in CONSOLE_SCRIPTS:
        src = os.path.join(git_pip_venv_dir(projects_yaml),
                           'bin/%s' % cs)
        link = '/usr/local/bin/%s' % cs
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(src, link)

    bin_dir = os.path.join(git_pip_venv_dir(projects_yaml), 'bin')
    glance_api_context = {
        'service_description': 'Astara Network Service Function Orchestrator',
        'service_name': 'Astara',
        'user_name': 'astara',
        'start_dir': '/var/lib/astara',
        'process_name': 'astara-orchestrator',
        'executable_name': os.path.join(bin_dir, 'astara-orchestrator'),
        'config_files': ['/etc/astara/rug.ini'],
        'log_file': '/var/log/astara/astara-orchestrator.log',
    }

    # NOTE(coreycb): Needs systemd support
    templates_dir = 'hooks/charmhelpers/contrib/openstack/templates'
    templates_dir = os.path.join(charm_dir(), templates_dir)
    render('git.upstart', '/etc/init/astara-orchestrator.conf',
           glance_api_context, perms=0o644, templates_dir=templates_dir)


def migrate_database():
    """Runs astara-dbsync to initialize a new database or migrate existing"""
    cmd = ['astara-dbsync', '--config-file', ASTARA_CONFIG, 'upgrade']
    subprocess.check_call(cmd)


def _auth_args():
    """Get current service credentials from the identity-service relation"""
    rel_ctxt = context.IdentityServiceContext()
    rel_data = rel_ctxt()
    if not context.context_complete(rel_data):
        juju_log('identity-service context not complete')
        return

    auth_url = '%s://%s:%s/v2.0' % (
        rel_data['service_protocol'],
        rel_data['service_host'],
        rel_data['service_port'])

    auth_args = {
        'username': rel_data['admin_user'],
        'password': rel_data['admin_password'],
        'tenant_name': rel_data['admin_tenant_name'],
        'auth_url': auth_url,
        'auth_strategy': 'keystone',
        'region': config('region'),
    }
    return auth_args


def get_or_create_network(client, name):
    for net in client.list_networks()['networks']:
        if net['name'] == name:
            return net
    res = client.create_network({
        'network': {
            'name': name,
        }
    })
    return res['network']

def get_or_create_subnet(client, cidr, network_id):

    for sn in client.list_subnets(network_id=network_id)['subnets']:
        if sn['cidr'] == cidr:
            return sn

    subnet = netaddr.IPNetwork(cidr)
    if subnet.version == 6:
        subnet_args = {
            'ip_version': 6,
            'ipv6_address_mode': 'slaac',
        }
    else:
        subnet_args = {
            'ip_version': 4,
        }

    subnet_args.update({
        'cidr': cidr,
        'network_id': network_id,
        'enable_dhcp': True,
    })

    res = client.create_subnet({'subnet': subnet_args})
    return res['subnet']

def create_management_network():
    """Creates a management network in Neutron to be used for
    orchestrator->appliance communication.
    """
    auth_args = _auth_args()
    if not auth_args:
        return
    client = NeutronClient.Client(**auth_args)

    mgt_net_cidr = config('management-network-cidr')
    mgt_net_name = config('management-network-name')

    subnet = netaddr.IPNetwork(mgt_net_cidr)
    if subnet.version == 6:
        subnet_args = {
            'ip_version': 6,
            'ipv6_address_mode': 'slaac',
        }
    else:
        subnet_args = {
            'ip_version': 4,
        }

    network = get_or_create_network(client, mgt_net_name)
    subnet = get_or_create_subnet(client, mgt_net_cidr, network['id'])

    # since this data is not available in any relation and to avoid a call
    # to neutron API for every config write out, save this data locally
    # for access from config context.
    net_config = {
        'management_network': network,
        'management_subnet': subnet,
    }
    with open(ASTARA_NETWORK_CACHE, 'w') as out:
        out.write(json.dumps(net_config))

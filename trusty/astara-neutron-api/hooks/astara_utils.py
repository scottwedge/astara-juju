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
import time

from collections import OrderedDict

from charmhelpers.contrib.openstack import context, templating

from charmhelpers.core.hookenv import (
    config,
    log as juju_log,
    relation_ids,
    relation_get,
    related_units,
)

from charmhelpers.contrib.openstack.utils import (
    git_install_requested,
    git_clone_and_install,
    git_pip_venv_dir,
)


CLIENT_RETRY_MAX = 20
TEMPLATES = 'templates/'
ASTARA_NETWORK_CACHE = '/var/lib/juju/astara-network-cache.json'
GLANCE_IMG_ID_CACHE = '/var/lib/juju/astara-applinace-image-cache'
NOVA_FLAVOR_ID_CACHE = '/var/lib/juju/astara-applinace-flavor-cache'

ASTARA_FLAVOR_NAME = 'astara'

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
    'python-neutronclient',
    'python-keystoneclient',
    'python-glanceclient',
    'python-novaclient',
]


def determine_packages():
    return BASE_GIT_PACKAGES


def validate_config():
    mgt_net = config('management-network-cidr')
    try:
        netaddr.IPNetwork(mgt_net)
    except netaddr.core.AddrFormatError as e:
        m = ('Invalid network CIDR configured for management-network-cidr: %s'
             % mgt_net)
        juju_log(m)
        raise e

    for i in ['astara-appliance-flavor-ram',
              'astara-appliance-flavor-cpu',
              'astara-appliance-flavor-disk']:
        try:
            v = config(i)
            int(v)
        except ValueError as e:
            juju_log('Invalid config %s=%s, must be integer value')
            raise e


def register_configs(release=None):
    resources = OrderedDict([
        ('/etc/neutron/plugins/ml2/ml2_conf.ini', {
            'services': [],
            'contexts': [],
        })
    ])

    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release='liberty')
    for cfg, rscs in resources.iteritems():
        configs.register(cfg, rscs['contexts'])
    return configs


def git_install(projects_yaml):
    """Perform setup, and install git repos specified in yaml parameter."""
    if git_install_requested():
        git_clone_and_install(projects_yaml, core_project='astara-neutron')


def _auth_args():
    """Get current service credentials from the identity-service relation"""
    rel_ctxt = context.IdentityServiceContext()
    rel_data = rel_ctxt()
    if not rel_data or not context.context_complete(rel_data):
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


def get_or_create_network(client, name, external=False):
    juju_log('get_or_create_network: %s' % name)
    for net in client.list_networks()['networks']:
        if net['name'] == name:
            juju_log('- found existing network: %s' % net['id'])
            return net
    juju_log('- creating new network: %s' % name)
    net_dict = {
        'network': {
            'name': name,
        }
    }
    if external:
        net_dict['network']['router:external'] = True

    res = client.create_network(net_dict)['network']
    juju_log('- created new network: %s( net_id: %s)' %
             (res['name'], res['id']))

    return res


def get_or_create_subnet(client, cidr, network_id):
    juju_log('get_or_create_subnet: %s (net: %s)' % (cidr, network_id))
    for sn in client.list_subnets(network_id=network_id)['subnets']:
        if sn['cidr'] == cidr:
            juju_log('- found existing subnet: %s' % sn['id'])
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

    juju_log('- creating new subnet: %s' % cidr)
    res = client.create_subnet({'subnet': subnet_args})['subnet']
    juju_log('- created new subnet: %s' % res['id'])
    return res


def _api_ready(relation, key):
    """Determines whether remote API service is ready.

    Inspects relation data to see if remote service advertises it's API service
    as ready via relation settings.
    """
    ready = 'no'
    for rid in relation_ids(relation):
        for unit in related_units(rid):
            ready = relation_get(attribute=key, unit=unit, rid=rid)
    return ready == 'yes'


def is_neutron_api_ready():
    return _api_ready('neutron-plugin-api-subordinate', 'neutron-api-ready')


def is_glance_api_ready():
    return _api_ready('image-service', 'glance-api-ready')


def is_nova_api_ready():
    return _api_ready('nova-api', 'nova-api-ready')


def ensure_client_connectivity(f):
    """Ensure a client can successfully call the server's API
    This is needed because remote service restarts are async. This could
    be removed if we can make restart_on_change() take an optional post-restart
    action it executes to ensure API is up before considering it restarted.

    :param f: A callable from a fully instantiated/configured client
              lib.
    """
    i = 0
    while i <= CLIENT_RETRY_MAX:
        try:
            f()
            juju_log(
                'Confirmed remote API connectivity /w %s after %s attempts' %
                (f, i))
            return
        except Exception as e:
            juju_log(
                'Failed to connect to remote API /w %s, retrying (%s/%s): %s' %
                (f, i, CLIENT_RETRY_MAX, e))
            i += 1
            time.sleep(1)

    raise Exception(
        'Failed to connect to remote API /w %s after %s retries.' %
        (f, CLIENT_RETRY_MAX))


def create_networks():
    """Gets or Creates a management network in Neutron to be used for
    orchestrator->appliance communication.

    The data about the network and subnet is cached locally to avoid future
    neutron calls.

    If we do not yet have a populated identity-service relation, this does
    nothing.

    :returns: A dict containing the network and subnet.
    """
    from neutronclient.v2_0 import client as NeutronClient
    auth_args = _auth_args()
    if not auth_args:
        return
    client = NeutronClient.Client(**auth_args)
    ensure_client_connectivity(client.list_networks)

    mgt_net = (
        config('management-network-cidr'), config('management-network-name'))
    ext_net = (
        config('external-network-cidr'), config('external-network-name'))

    networks = []
    subnets = []
    for net in [mgt_net, ext_net]:
        if net == ext_net:
            external = True
        else:
            external = False

        net_cidr, net_name = net
        network = get_or_create_network(client, net_name, external)
        networks.append(network)
        subnets.append(get_or_create_subnet(client, net_cidr, network['id']))

    # since this data is not available in any relation and to avoid a call
    # to neutron API for every config write out, save this data locally
    # for access from config context.
    net_data = {
        'networks': networks,
        'subnets': subnets,
    }
    with open(ASTARA_NETWORK_CACHE, 'w') as out:
        out.write(json.dumps(net_data))

    return net_data


def get_keystone_session(auth_args):
    from keystoneclient import auth as ksauth
    from keystoneclient import session as kssession

    auth_plugin = ksauth.get_plugin_class('password')
    auth = auth_plugin(
        auth_url=auth_args['auth_url'],
        username=auth_args['username'],
        password=auth_args['password'],
        project_name=auth_args['tenant_name'],
    )
    return kssession.Session(auth=auth)


def get_novaclient(auth_args):
    from novaclient import client
    ks_session = get_keystone_session(auth_args)
    nc = client.Client(
        version='2',
        session=ks_session,
        region_name=config('region'),
    )
    return nc


def get_glanceclient(auth_args):
    from glanceclient.v2.client import Client
    ks_session = get_keystone_session(auth_args)
    return Client(session=ks_session)


def get_appliance_image(img_loc):
    juju_log('Downloading astara appliance from %s' % img_loc)
    img_name = img_loc.split('/')[-1]
    subprocess.check_output([
        'wget', '-O', '/tmp/%s' % img_name, img_loc
    ])


def _cache_img(img):
    with open(GLANCE_IMG_ID_CACHE, 'w') as out:
        out.write(img['id'])


def appliance_image():
    img_loc = config('astara-router-appliance-url')
    img_name = img_loc.split('/')[-1]
    return img_name, img_loc


def publish_astara_appliance_image():
    """Downloads and publishes the appliance image into Glance

    If an image by the same name exists already, we do not publish.

    If we do not yet have a populated identity-service relation, this does
    nothing.

    In any case, the published or found image is cached locally to avoid
    glance calls in the future.

    Note: We're currently publishing qcow2's and will need to make this
    flexible to do a conversion and publish in raw for Ceph backed clouds.
    """
    auth_args = _auth_args()
    if not auth_args:
        return
    client = get_glanceclient(auth_args)
    ensure_client_connectivity(client.images.list)

    img_name, img_loc = appliance_image()

    for img in client.images.list():
        if img['name'] == img_name:
            _cache_img(img)
            juju_log(
                'Image named %s already exists in glance, skipping publish.' %
                img_name)
            return

    juju_log('Downloading astara appliance from %s' % img_loc)
    subprocess.check_output([
        'wget', '-O', '/tmp/%s' % img_name, img_loc
    ])

    juju_log('Publishing appliance image into glance')
    glance_img = client.images.create(
        name=img_name,
        container_format='bare',
        disk_format='qcow2')
    client.images.upload(
        image_id=glance_img['id'],
        image_data=open('/tmp/%s' % img_name))

    _cache_img(glance_img)
    return glance_img['id']


def appliance_image_uuid():
    """Returns the UUID of the published appliance image"""
    if os.path.isfile(GLANCE_IMG_ID_CACHE):
        return open(GLANCE_IMG_ID_CACHE).read().strip()
    else:
        return publish_astara_appliance_image()


def create_astara_nova_flavor():
    """Gets or creates the astara appliance nova flavor

    Get or create the nova flavor for the appliance. Cache it
    locally and return its id.

    :returns: str id of the nova flavor
    """
    auth_args = _auth_args()
    if not auth_args:
        return
    novaclient = get_novaclient(auth_args)
    existing = [f for f in novaclient.flavors.list()
                if f.name == ASTARA_FLAVOR_NAME]
    if existing:
        flavor = existing[0]
    else:
        flavor = novaclient.flavors.create(
            name=ASTARA_FLAVOR_NAME,
            ram=int(config('astara-appliance-flavor-ram')),
            disk=int(config('astara-appliance-flavor-disk')),
            vcpus=int(config('astara-appliance-flavor-cpu')),
        )
    with open(NOVA_FLAVOR_ID_CACHE, 'w') as out:
        out.write(flavor.id)

    return flavor.id


def appliance_flavor_id():
    if os.path.isfile(NOVA_FLAVOR_ID_CACHE):
        return open(NOVA_FLAVOR_ID_CACHE).read().strip()
    else:
        return create_astara_nova_flavor()


def get_network(net_type='management'):
    """Returns the dict of network + subnet data for the management network"""
    if os.path.isfile(ASTARA_NETWORK_CACHE):
        net_data = json.loads(open(ASTARA_NETWORK_CACHE).read())
    else:
        net_data = create_networks()

    if not net_data:
        return {}

    net_name = config('%s-network-name' % net_type)
    subnet_cidr = config('%s-network-cidr' % net_type)
    network = None
    subnet = None
    for net in net_data['networks']:
        if net['name'] == net_name:
            network = net
    for snet in net_data['subnets']:
        if snet['cidr'] == subnet_cidr:
            subnet = snet
    return {
        'network': network,
        'subnet': subnet,
    }


def api_extensions_path():
    """Return need the full path to the installation of neutron API extensions
    This is dependent on how the library was installed (git vs pkg)
    """
    if git_install_requested():
        venv_dir = git_pip_venv_dir(config('openstack-origin-git'))
        py = os.path.join(venv_dir, 'bin', 'python')
    else:
        py = subprocess.check_output(['which', 'python']).strip()

    cmd = [
        py, '-c', 'from akanda import neutron; print neutron.__file__;'
    ]
    module_path = subprocess.check_output(cmd)
    ext_path = os.path.join(os.path.dirname(module_path), 'extensions')

    if not os.path.isdir(ext_path):
        m = ('Could not locate astara-neutron API extensions directory @ %s' %
             ext_path)
        juju_log(m, 'ERROR')
        raise Exception(m)

    return ext_path

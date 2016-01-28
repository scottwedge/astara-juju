#!/usr/bin/python
#
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
import subprocess
import sys
import uuid


from charmhelpers.fetch import (
    apt_install,
    apt_update,
)


from charmhelpers.core.hookenv import (
    Hooks,
    config,
    log as juju_log,
    relation_ids,
    relation_set,
    UnregisteredHookError,
)


from astara_utils import (
    api_extensions_path,
    appliance_image_uuid,
    appliance_flavor_id,
    create_networks,
    create_astara_nova_flavor,
    determine_packages,
    get_network,
    git_install,
    is_glance_api_ready,
    is_neutron_api_ready,
    is_nova_api_ready,
    publish_astara_appliance_image,
    register_configs,
    validate_config,
)


from charmhelpers.contrib.openstack.utils import (
    config_value_changed,
    git_install_requested,
)


from charmhelpers.contrib.openstack.ip import canonical_url, ADMIN


hooks = Hooks()
CONFIGS = register_configs()


@hooks.hook('identity-service-relation-joined')
def keystone_joined(relation_id=None):
    # TODO(adam_g): This will actually need to happen in astara-orchestrator
    url = '{}:44250'.format(canonical_url(configs=None, endpoint_type=ADMIN))
    relation_data = {
        'service': 'astara',
        'region': config('region'),
        'public_url': url,
        'admin_url': url,
        'internal_url': url,
    }
    relation_set(relation_id=relation_id, **relation_data)


@hooks.hook('identity-service-relation-changed')
def keystone_changed():
    # once we have sufficent keystone creds and the neutron ap is ready, we
    # can create networks in neutron and images in glance, then advertise
    # those to the orchestrator.
    if is_neutron_api_ready():
        create_networks()
    if is_glance_api_ready():
        publish_astara_appliance_image()
    for rid in relation_ids('astara-orchestrator'):
        astara_orchestrator_relation_joined(rid)


@hooks.hook('config-changed')
def config_changed():
    if git_install_requested():
        if config_value_changed('openstack-origin-git'):
            git_install(config('openstack-origin-git'))
    validate_config()
    CONFIGS.write_all()


@hooks.hook('neutron-plugin-api-subordinate-relation-changed')
def neutron_api_changed(rid=None):
    if not is_neutron_api_ready():
        return
    create_networks()
    for rid in relation_ids('astara-orchestrator'):
        astara_orchestrator_relation_joined(rid)


@hooks.hook('neutron-plugin-api-subordinate-relation-joined')
def neutron_api_joined(rid=None):
    sub_config = {
        'neutron-api': {
            '/etc/neutron/neutron.conf': {
                'sections': {
                    'DEFAULT': [
                        ('api_extensions_path', api_extensions_path())
                    ]
                }
            },
        },
    }
    # Set ml2_conf.ini
    CONFIGS.write_all()
    relation_settings = {
        'neutron-plugin': 'astara',
        'core-plugin': 'akanda.neutron.plugins.ml2_neutron_plugin.Ml2Plugin',
        'service-plugins':
        'akanda.neutron.plugins.ml2_neutron_plugin.L3RouterPlugin',
        'subordinate_configuration': json.dumps(sub_config),
        'restart-trigger': str(uuid.uuid4()),
        'migration-configs': ['/etc/neutron/plugins/ml2/ml2_conf.ini'],
    }
    relation_set(relation_settings=relation_settings)


@hooks.hook('image-service-relation-changed')
def image_service_relation_changed():
    # If the API is ready we publish the astara appliance(s) into Glance
    # and advertise those to the orchestrator.
    if not is_glance_api_ready():
        return
    publish_astara_appliance_image()
    for rid in relation_ids('astara-orchestrator'):
        astara_orchestrator_relation_joined(rid)


@hooks.hook('nova-api-relation-changed')
def nova_api_relation_changed():
    if not is_nova_api_ready():
        return
    create_astara_nova_flavor()
    for rid in relation_ids('astara-orchestrator'):
        astara_orchestrator_relation_joined(rid)


@hooks.hook('astara-orchestrator-relation-joined')
def astara_orchestrator_relation_joined(rid=None):
    # Inform the orchestrator about the neutron networks and glance images
    appliance_image = appliance_image_uuid()
    mgt_net_data = get_network('management') or {}
    flavor_id = appliance_flavor_id()
    if not appliance_image or not mgt_net_data or not flavor_id:
        juju_log(
            'No published image, created networks or created flavors to '
            'advertise to astara-orchestrator.')
        return

    mgt_net_data = get_network('management') or {}
    mgt_network = mgt_net_data.get('network', {})
    mgt_subnet = mgt_net_data.get('subnet', {})
    ext_net_data = get_network('external') or {}
    ext_network = ext_net_data.get('network', {})
    ext_subnet = ext_net_data.get('subnet', {})

    required_data = [mgt_network, mgt_subnet, ext_network, ext_subnet,
                     appliance_image]

    for d in required_data:
        if not d:
            juju_log(
                'No published image or created networks to advertise to '
                'astara-orchestrator.')
            return

    relation_data = {
        'management_network_id': mgt_network.get('id'),
        'management_subnet_id': mgt_subnet.get('id'),
        'management_prefix': mgt_subnet.get('cidr'),
        'external_network_id': ext_network.get('id'),
        'external_subnet_id': ext_subnet.get('id'),
        'external_prefix': ext_subnet.get('cidr'),
        'router_image_uuid': appliance_image,
        'appliance_flavor_id': flavor_id,
    }
    relation_set(relation_id=rid, **relation_data)


@hooks.hook('install')
def install():
    apt_update(fatal=True)
    apt_install(determine_packages(), fatal=True)
    if git_install_requested():
        git_install(config('openstack-origin-git'))

        # NOTE(adam_g):
        # something gets screwy with the requests packages being pulled in
        # as python-requests-whl /w python-pip-whl and via the UCA, causing
        # glanceclient usage in the charm to break.  Purge it here and let it
        # be reinstalled during next hook exec, that seems to fix the issue?
        cmd = ['dpkg', '-P', 'python-pip-whl', 'python-requests-whl',
               'python-pip']
        subprocess.check_call(cmd)


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        juju_log('Unknown hook {} - skipping.'.format(e))


if __name__ == '__main__':
    main()

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
    local_unit,
    relation_get,
    relation_ids,
    relation_set,
    status_set,
    unit_get,
    UnregisteredHookError,
)


from astara_utils import (
    api_extensions_path,
    appliance_image_uuid,
    create_networks,
    determine_packages,
    get_network,
    git_install,
    is_glance_api_ready,
    is_neutron_api_ready,
    migrate_database,
    publish_astara_appliance_image,
    register_configs,
)


from charmhelpers.contrib.openstack.utils import (
    config_value_changed,
    sync_db_with_multi_ipv6_addresses,
    git_install_requested,
)


from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    PUBLIC, INTERNAL, ADMIN
)


hooks = Hooks()
CONFIGS = register_configs()

@hooks.hook('shared-db-relation-joined')
def db_joined():
    if config('prefer-ipv6'):
        sync_db_with_multi_ipv6_addresses(config('database'),
                                          config('database-user'))
    else:
        host = unit_get('private-address')
        relation_set(database=config('database'),
                     username=config('database-user'),
                     hostname=host)


@hooks.hook('shared-db-relation-changed')
def db_changed():
    if 'shared-db' not in CONFIGS.complete_contexts():
        juju_log('shared-db relation incomplete. Peer not ready?')
        return

    CONFIGS.write(ASTARA_CONFIG)

    # XXX (This is where leadership election will go)
    #if is_elected_leader(CLUSTER_RES):
    if True:
        # Bugs 1353135 & 1187508. Dbs can appear to be ready before the units
        # acl entry has been added. So, if the db supports passing a list of
        # permitted units then check if we're in the list.
        allowed_units = relation_get('allowed_units')
        if allowed_units and local_unit() in allowed_units.split():
            juju_log('Cluster leader, performing db sync')
            migrate_database()
        else:
            juju_log('allowed_units either not presented, or local unit '
                     'not in acl list: %s' % allowed_units)

@hooks.hook('identity-service-relation-joined')
def keystone_joined(relation_id=None):
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


@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    relation_set(
        relation_id=relation_id,
        username=config('rabbit-user'),
        vhost=config('rabbit-vhost'))


@hooks.hook('amqp-relation-changed')
@hooks.hook('amqp-relation-departed')
def amqp_changed():
    if 'amqp' not in CONFIGS.complete_contexts():
        juju_log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write(ASTARA_CONFIG)


@hooks.hook('config-changed')
def config_changed():
    return
    global CONFIGS

    if git_install_requested():
        if config_value_changed('openstack-origin-git'):
            status_set('maintenance', 'Running Git install')
            git_install(config('openstack-origin-git'))

    status_set('maintenance', 'Configuring Astara')
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
        'service-plugins': 'akanda.neutron.plugins.ml2_neutron_plugin.L3RouterPlugin',
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


@hooks.hook('astara-orchestrator-relation-joined')
def astara_orchestrator_relation_joined(rid=None):
    appliance_image = appliance_image_uuid()
    mgt_net_data = get_network('management') or {}
    if not appliance_image or not mgt_net_data:
        juju_log(
            'No published image or created networks to advertise to '
            'astara-orchestrator.')
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
    }
    relation_set(relation_id=rid, **relation_data)


@hooks.hook('install')
def install():
    status_set('maintenance', 'Installing astara-neutron')
    apt_update(fatal=True)
    apt_install(determine_packages(), fatal=True)
    if git_install_requested():
        status_set('maintenance', 'Installing Astara (via git)')
        git_install(config('openstack-origin-git'))

        # something gets screwy with the requests packages being pulled in
        # as python-requests-whl via pip and via the UCA, causing glanceclient
        # usage in the charm to break.  Purge it here and let it be
        # reinstalled during next hook exec, that seems to fix the issue?
        cmd = ['dpkg', '-P', 'python-pip-whl', 'python-requests-whl',
               'python-pip']
        subprocess.check_call(cmd)


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        juju_log('Unknown hook {} - skipping.'.format(e))
#    set_os_workload_status(CONFIGS, REQUIRED_INTERFACES,
#                           charm_func=check_optional_relations)
#

if __name__ == '__main__':
    main()

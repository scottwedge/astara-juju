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

import sys

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
    relation_set,
    status_set,
    unit_get,
    UnregisteredHookError,
)

from charmhelpers.core.host import (
    restart_on_change,
)


from astara_utils import (
    create_management_network,
    determine_packages,
    migrate_database,
    register_configs,
    restart_map,
    git_install,
    ASTARA_CONFIG,
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
@restart_on_change(restart_map())
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
    public_url = '{}:44250'.format(canonical_url(CONFIGS, PUBLIC))
    internal_url = '{}:44250'.format(canonical_url(CONFIGS, INTERNAL))
    admin_url = '{}:44250'.format(canonical_url(CONFIGS, ADMIN))
    relation_data = {
        'service': 'astara',
        'region': config('region'),
        'public_url': public_url,
        'admin_url': admin_url,
        'internal_url': internal_url,
    }
    relation_set(relation_id=relation_id, **relation_data)


@hooks.hook('identity-service-relation-changed')
@restart_on_change(restart_map())
def keystone_changed():
    if 'identity-service' not in CONFIGS.complete_contexts():
        juju_log('identity-service relation incomplete. Peer not ready?')
        return

    CONFIGS.write(ASTARA_CONFIG)

    #configure_https()


@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    relation_set(
        relation_id=relation_id,
        username=config('rabbit-user'),
        vhost=config('rabbit-vhost'))


@hooks.hook('amqp-relation-changed')
@hooks.hook('amqp-relation-departed')
@restart_on_change(restart_map())
def amqp_changed():
    if 'amqp' not in CONFIGS.complete_contexts():
        juju_log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write(ASTARA_CONFIG)


@hooks.hook('config-changed')
@restart_on_change(restart_map(), stopstart=True)
def config_changed():
    global CONFIGS

    if git_install_requested():
        if config_value_changed('openstack-origin-git'):
            status_set('maintenance', 'Running Git install')
            git_install(config('openstack-origin-git'))

    status_set('maintenance', 'Configuring Astara')
    CONFIGS.write_all()


@hooks.hook('neutron-api-relation-changed')
@restart_on_change(restart_map(), stopstart=True)
def neutron_api_changed(rid=None):
    api_ready = relation_get('neutron-api-ready')
    if not api_ready:
        return
    if api_ready.lower() != 'yes':
        return
    create_management_network()
    CONFIGS.write_all()


@hooks.hook('install')
def install():
    status_set('maintenance', 'Installing Dependencies')
    apt_update(fatal=True)
    apt_install(determine_packages(), fatal=True)
    if git_install_requested():
        status_set('maintenance', 'Installing Astara (via git)')
        git_install(config('openstack-origin-git'))

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

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

from charmhelpers.core.hookenv import (
    relation_ids,
    related_units,
    relation_get,
    log,
)

from charmhelpers.contrib.openstack import context


class AstaraOrchestratorContext(context.OSContextGenerator):
    interfaces = ['astara-orchestrator']
    astara_neutron_keys = [
        'external_network_id',
        'external_subnet_id',
        'external_prefix',
        'management_network_id',
        'management_subnet_id',
        'management_prefix',
        'router_image_uuid',
        'appliance_flavor_id',
    ]

    def _coordinator_context(self):
        """Attempt to create a usable tooz coordinator URL from zk or memcache

        This'll see if we have zookeeper or memcached relations and use that
        found as the coordinator. Note memcahe is only for testing and
        zookeeper will be preferred if both are found.
        """

        # NOTE: Neither the zookeeper or memcache charms do any kind of
        # clustering of peers, so we just look for one that tells us its
        # port and point at that.
        zk_relation_ids = relation_ids('zookeeper')
        for rid in zk_relation_ids:
            for unit in related_units(rid):
                rel_data = relation_get(unit=unit, rid=rid)
                zk_port = rel_data.get('port')
                zk_addr = rel_data.get('private-address')
                if zk_port:
                    url = 'kazoo://%s:%s?timeout=5' % (zk_addr, zk_port)
                    log('Using zookeeper @ %s for astara coordination' % url)
                    return {'coordination_url': url}

        memcached_relation_ids = relation_ids('cache')
        for rid in memcached_relation_ids:
            for unit in related_units(rid):
                rel_data = relation_get(unit=unit, rid=rid)
                mc_port = rel_data.get('tcp-port')
                mc_addr = rel_data.get('private-address')
                if mc_port:
                    url = 'mecached://%s:%s' % (mc_port, mc_addr)
                    log('Using memcached @ %s for astara coordination' % url)
                    return {'coordination_url': url}

        log('no astara coordination relation data found')
        return {}

    def _astara_context(self):
        for rid in relation_ids('astara-orchestrator'):
            for unit in related_units(rid):
                ctxt = {}
                rel_data = relation_get(unit=unit, rid=rid)
                for k in self.astara_neutron_keys:
                    ctxt[k] = rel_data.get(k)
                if None not in ctxt.values():
                    return ctxt
        log('astara-orchestrator relation data incomplete.')
        return {}

    def __call__(self):
        ctxt = self._astara_context()
        if not ctxt:
            return {}
        coord_url = self._coordinator_context()
        if coord_url:
            ctxt.update({'coordination_enabled': True})
            ctxt.update(coord_url)
        return ctxt

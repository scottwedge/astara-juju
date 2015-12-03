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
    keys = [
        'external_network_id',
        'external_subnet_id',
        'external_prefix',
        'management_network_id',
        'management_subnet_id',
        'management_prefix',
        'router_image_uuid',
    ]

    def __call__(self):
        for rid in relation_ids('astara-orchestrator'):
            for unit in related_units(rid):
                ctxt = {}
                rel_data = relation_get(unit=unit, rid=rid)
                for k in self.keys:
                    ctxt[k] = rel_data.get(k)
                if None not in ctxt.values():
                    return ctxt
        log('astara-orchestrator relation data incomplete.')

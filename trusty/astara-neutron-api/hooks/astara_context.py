
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

from charmhelpers.contrib.openstack import context


class AstaraContext(context.OSContextGenerator):
    interfaces = []

    def __init__(self, network_cache):
        """ Astara charm-local context

        :param network_cache: Full path to json dump of mgt network and subnet
                              created in
                              astara_utils:create_management_network()
        """
        self.cache_file = network_cache

    def __call__(self):
        try:
            cache_in = json.loads(open(self.cache_file).read())
        except:
            return {}
        network = cache_in.get('management_network')
        subnet = cache_in.get('management_subnet')
        return {
            'management_network_id': network.get('id'),
            'management_subnet_id':  subnet.get('id'),
            'management_network_prefix': subnet.get('cidr'),
        }

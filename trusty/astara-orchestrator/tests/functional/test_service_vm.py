import ConfigParser
import logging
import tempfile
import time

import base

LOG = logging.getLogger(__name__)


class AstaraServiceVMTest(base.AstaraFunctionalBase):
    # These tests validate the Astara service is functioning properly
    @classmethod
    def setUpClass(cls):
        super(AstaraServiceVMTest, cls).setUpClass()
        cls.tenant = cls.get_tenant()
        cls.neutronclient = cls.tenant.clients.neutronclient

    def test_router_creation_recovery(self):
        """Tenant router creation results in a service appliance booting"""
        # This tests that a tenant can create a router and an appliance VM
        # is spawned on the backend.  It also tests that a new appliance VM
        # is spawned by Astara if the original is deleted.
        # we have no instances running anywhere

        self.assertEqual(
            self.admin_clients.novaclient.servers.list(
                search_opts=dict(all_tenants=1)),
            [])

        # tenant creates a net, subnet, router
        network, router = self.tenant.setup_networking()

        # wait for a nova instance to spawn for the router and become active
        nova_instance = self.get_router_appliance_server(
            router_uuid=router['id'], retries=60)

        # for each subnet that was created during setup, ensure we have a
        # router interface added
        ports = self.neutronclient.list_ports()['ports']
        subnets = self.neutronclient.list_subnets(
            network_id=network['id'])
        subnets = subnets['subnets']
        self.assertEqual(len(ports), len(subnets))
        for port in ports:
            self.assertEqual(port['device_owner'], 'network:router_interface')
            self.assertEqual(port['device_id'], router['id'])
            self.assertEqual(
                sorted([subnet['id'] for subnet in subnets]),
                sorted([fip['subnet_id'] for fip in port['fixed_ips']])
            )

        # Ensure that if we destroy the nova instance, the RUG will rebuild
        # the router with a new instance.
        # This could live in a separate test case but it'd require the
        # above as setup, so just piggyback on it.

        old_server = nova_instance
        LOG.debug('Original server: %s', old_server)

        # NOTE(adam_g): In the gate, sometimes the appliance hangs on the
        # first config update and health checks get queued up behind the
        # hanging config update.  If thats the case, we need to wait a while
        # before deletion for the first to timeout.
        time.sleep(30)
        LOG.debug('Deleting original nova server: %s', old_server.id)
        self.admin_clients.novaclient.servers.delete(old_server.id)

        LOG.debug('Waiting %s seconds for astara health check to tick',
                  60)
        time.sleep(60)

        # look for the new server, retry giving rug time to do its thing.
        new_server = self.get_router_appliance_server(
            router['id'], retries=60, wait_for_active=True)
        LOG.debug('Rebuilt new server found: %s', new_server)
        self.assertNotEqual(old_server.id, new_server.id)

        # routers report as ACTIVE initially (LP: #1491673)
        time.sleep(2)

        self.assert_router_is_active(router['id'])


class AstaraJujuServiceTest(base.AstaraFunctionalBase):
    # These tests validate the actual juju deployment

    @classmethod
    def setUpClass(cls):
        super(AstaraJujuServiceTest, cls).setUpClass()
        # the charm config for the astara-neutron-api subordinate
        cls.astara_neutron_api_config = base.get_config('astara-neutron-api')

        # the actual on-disk config for the astara-orchestrator service
        astara_orchestrator_config = cls.juju_ssh(
            'astara-orchestrator/0', 'cat /etc/astara/orchestrator.ini')
        with tempfile.TemporaryFile() as fp:
            fp.write(astara_orchestrator_config)
            fp.seek(0)
            cls.astara_orchestrator_config = ConfigParser.ConfigParser()
            cls.astara_orchestrator_config.readfp(fp)

    def test_nova_flavor_created_and_configured(self):
        """Flavor is created as per charm configuration and set accordingly"""
        flavor_ram = self.astara_neutron_api_config.get(
            'astara-appliance-flavor-ram')['value']
        flavor_cpu = self.astara_neutron_api_config.get(
            'astara-appliance-flavor-cpu')['value']
        flavor_disk = self.astara_neutron_api_config.get(
            'astara-appliance-flavor-disk')['value']

        # find the created flavor and ensure its specs
        astara_flavor = [
            f for f in self.admin_clients.novaclient.flavors.list()
            if f.name == 'astara'][0]
        self.assertEqual(astara_flavor.ram, flavor_ram)
        self.assertEqual(astara_flavor.vcpus, flavor_cpu)
        self.assertEqual(astara_flavor.disk, flavor_disk)

        # ensure the flavor id was passed from the astara-neutrona-api charm
        # to the astara-orchestrator charm
        configured_flavor = self.astara_orchestrator_config.get(
            'router', 'instance_flavor')
        self.assertEqual(configured_flavor, astara_flavor.id)

    def test_glance_image_created_and_configured(self):
        """Appliance image was uploaded to glance and set accordingly"""
        image_url = self.astara_neutron_api_config.get(
            'astara-router-appliance-url')['value']
        image_name = image_url.split('/')[-1:][0]
        astara_img = [
            img for img in self.admin_clients.novaclient.images.list()
            if img.name == image_name][0]
        configured_img = self.astara_orchestrator_config.get(
            'router', 'image_uuid')
        self.assertEqual(configured_img, astara_img.id)

    def test_neutron_networks_created_and_configured(self):
        mgt_net_cidr = self.astara_neutron_api_config.get(
            'management-network-cidr')['value']
        mgt_net_name = self.astara_neutron_api_config.get(
            'management-network-name')['value']

        ext_net_cidr = self.astara_neutron_api_config.get(
            'external-network-cidr')['value']
        ext_net_name = self.astara_neutron_api_config.get(
            'external-network-name')['value']

        # mgt network and subnet have been created
        mgt_net = self.admin_clients.neutronclient.list_networks(
            name=mgt_net_name)['networks'][0]
        mgt_subnets = [
            self.admin_clients.neutronclient.list_subnets(id=sid)['subnets'][0]
            for sid in mgt_net['subnets']]
        self.assertIn(
            mgt_net_cidr,
            [s.get('cidr') for s in mgt_subnets])

        # external network have been created and marked external
        ext_net = self.admin_clients.neutronclient.list_networks(
            name=ext_net_name)['networks'][0]
        ext_subnets = [
            self.admin_clients.neutronclient.list_subnets(id=sid)['subnets'][0]
            for sid in ext_net['subnets']]
        self.assertEqual(
            ext_net['router:external'], True)

        self.assertIn(
            ext_net_cidr,
            [s.get('cidr') for s in ext_subnets])

        # ensure the proper net/subnet ids have been passed over to the
        # orchestrator
        self.assertEqual(
            self.astara_orchestrator_config.get(
                'DEFAULT', 'external_network_id'),
            ext_net['id'])
        self.assertIn(
            self.astara_orchestrator_config.get(
                'DEFAULT', 'external_subnet_id'),
            [s['id'] for s in ext_subnets])

        self.assertEqual(
            self.astara_orchestrator_config.get(
                'DEFAULT', 'management_network_id'),
            mgt_net['id'])
        self.assertIn(
            self.astara_orchestrator_config.get(
                'DEFAULT', 'management_subnet_id'),
            [s['id'] for s in mgt_subnets])

from mock import patch
import os

os.environ['JUJU_UNIT_NAME'] = 'keystone'

with patch('keystone_utils.register_configs') as register_configs:
    import openstack_upgrade
    import keystone_hooks as hooks

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'do_openstack_upgrade',
    'os',
]


class TestKeystoneUpgradeActions(CharmTestCase):

    def setUp(self):
        super(TestKeystoneUpgradeActions, self).setUp(openstack_upgrade,
                                                      TO_PATCH)

    @patch.object(hooks, 'register_configs')
    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    @patch('charmhelpers.contrib.openstack.utils.git_install_requested')
    @patch('charmhelpers.contrib.openstack.utils.openstack_upgrade_available')
    def test_openstack_upgrade_true(self, upgrade_avail, git_requested,
                                    action_set, config, reg_configs):
        git_requested.return_value = False
        upgrade_avail.return_value = True
        config.return_value = True

        openstack_upgrade.openstack_upgrade()

        self.assertTrue(self.do_openstack_upgrade.called)
        self.os.execl.assert_called_with('./hooks/config-changed-postupgrade',
                                         '')

    @patch.object(hooks, 'register_configs')
    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    @patch('charmhelpers.contrib.openstack.utils.git_install_requested')
    @patch('charmhelpers.contrib.openstack.utils.openstack_upgrade_available')
    def test_openstack_upgrade_false(self, upgrade_avail, git_requested,
                                     action_set, config, reg_configs):
        git_requested.return_value = False
        upgrade_avail.return_value = True
        config.return_value = False

        openstack_upgrade.openstack_upgrade()

        self.assertFalse(self.do_openstack_upgrade.called)
        self.assertFalse(self.os.execl.called)

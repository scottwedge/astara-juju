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

import os
import subprocess

from collections import OrderedDict

import astara_context

from charmhelpers.core.hookenv import charm_dir
from charmhelpers.contrib.openstack import context, templating
from charmhelpers.contrib.python.packages import pip_install
from charmhelpers.core.templating import render


from charmhelpers.core.host import (
    adduser,
    add_group,
    add_user_to_group,
    mkdir,
    write_file,
)


from charmhelpers.contrib.openstack.utils import (
    git_install_requested,
    git_clone_and_install,
    git_yaml_value,
    git_pip_venv_dir,
)


TEMPLATES = 'templates/'
ASTARA_CONFIG = '/etc/astara/orchestrator.ini'

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
                astara_context.AstaraOrchestratorContext(),
                context.AMQPContext(),
                context.SharedDBContext(),
                context.IdentityServiceContext(
                    service='astara',
                    service_user='astara'),
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
    astara_orchestrator_context = {
        'service_description': 'Astara Network Service Function Orchestrator',
        'service_name': 'Astara',
        # NOTE(adam_g): need to run as root untiul oslo.rootwrap integration
        # is added:
        # https://blueprints.launchpad.net/astara/+spec/astara-rootwrap
        'user_name': 'root',
        'start_dir': '/var/lib/astara',
        'process_name': 'astara-orchestrator',
        'executable_name': os.path.join(bin_dir, 'astara-orchestrator'),
        'config_files': ['/etc/astara/orchestrator.ini'],
        'log_file': '/var/log/astara/astara-orchestrator.log',
    }

    # NOTE(coreycb): Needs systemd support
    templates_dir = 'hooks/charmhelpers/contrib/openstack/templates'
    templates_dir = os.path.join(charm_dir(), templates_dir)
    render('git.upstart', '/etc/init/astara-orchestrator.conf',
           astara_orchestrator_context, perms=0o644,
           templates_dir=templates_dir)


def migrate_database():
    """Runs astara-dbsync to initialize a new database or migrate existing"""
    cmd = ['astara-dbsync', '--config-file', ASTARA_CONFIG, 'upgrade']
    subprocess.check_call(cmd)

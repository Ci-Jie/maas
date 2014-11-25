# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test for RPC utility functions for Nodes."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import random

from django.core.exceptions import ValidationError
from maasserver.enum import NODE_STATUS
from maasserver.rpc.nodes import (
    create_node,
    mark_node_failed,
    request_node_info_by_mac_address,
    )
from maasserver.rpc.testing.fixtures import MockLiveRegionToClusterRPCFixture
from maasserver.testing.architecture import make_usable_architecture
from maasserver.testing.eventloop import (
    RegionEventLoopFixture,
    RunningEventLoopFixture,
    )
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import MAASServerTestCase
from provisioningserver.drivers import PowerTypeRegistry
from provisioningserver.rpc.cluster import DescribePowerTypes
from provisioningserver.rpc.exceptions import (
    NodeAlreadyExists,
    NodeStateViolation,
    NoSuchNode,
    )
from provisioningserver.rpc.testing import always_succeed_with
from simplejson import dumps


class TestCreateNode(MAASServerTestCase):

    def prepare_cluster_rpc(self, cluster):
        self.useFixture(RegionEventLoopFixture('rpc'))
        self.useFixture(RunningEventLoopFixture())

        fixture = self.useFixture(MockLiveRegionToClusterRPCFixture())
        protocol = fixture.makeCluster(cluster, DescribePowerTypes)
        self.power_types = [item for name, item in PowerTypeRegistry]
        protocol.DescribePowerTypes.side_effect = always_succeed_with(
            {'power_types': self.power_types})
        return protocol

    def test_creates_node(self):
        cluster = factory.make_NodeGroup()
        cluster.accept()
        self.prepare_cluster_rpc(cluster)

        mac_addresses = [
            factory.make_mac_address() for _ in range(3)]
        architecture = make_usable_architecture(self)
        power_type = random.choice(self.power_types)['name']
        power_parameters = dumps({})

        node = create_node(
            cluster.uuid, architecture, power_type, power_parameters,
            mac_addresses)

        self.assertEqual(
            (
                cluster,
                architecture,
                power_type,
                {},
            ),
            (
                node.nodegroup,
                node.architecture,
                node.power_type,
                node.power_parameters
            ))
        self.assertItemsEqual(
            mac_addresses,
            [mac.mac_address for mac in node.macaddress_set.all()])

    def test_raises_validation_errors_for_invalid_data(self):
        cluster = factory.make_NodeGroup()
        cluster.accept()
        self.prepare_cluster_rpc(cluster)

        self.assertRaises(
            ValidationError, create_node, cluster.uuid,
            architecture="spam/eggs", power_type="scrambled",
            power_parameters=dumps({}),
            mac_addresses=[factory.make_mac_address()])

    def test__raises_error_if_node_already_exists(self):
        cluster = factory.make_NodeGroup()
        cluster.accept()
        self.prepare_cluster_rpc(cluster)

        mac_addresses = [
            factory.make_mac_address() for _ in range(3)]
        architecture = make_usable_architecture(self)
        power_type = random.choice(self.power_types)['name']
        power_parameters = dumps({})

        create_node(
            cluster.uuid, architecture, power_type, power_parameters,
            mac_addresses)
        self.assertRaises(
            NodeAlreadyExists, create_node, cluster.uuid, architecture,
            power_type, power_parameters, [mac_addresses[0]])

    def test__saves_power_parameters(self):
        cluster = factory.make_NodeGroup()
        cluster.accept()
        self.prepare_cluster_rpc(cluster)

        mac_addresses = [
            factory.make_mac_address() for _ in range(3)]
        architecture = make_usable_architecture(self)
        power_type = random.choice(self.power_types)['name']
        power_parameters = {
            factory.make_name('key'): factory.make_name('value')
            for _ in range(3)
        }

        node = create_node(
            cluster.uuid, architecture, power_type, dumps(power_parameters),
            mac_addresses)

        # Reload the object from the DB so that we're sure its power
        # parameters are being persisted.
        node = reload_object(node)
        self.assertEqual(power_parameters, node.power_parameters)

    def test__forces_generic_subarchitecture_if_missing(self):
        cluster = factory.make_NodeGroup()
        cluster.accept()
        self.prepare_cluster_rpc(cluster)

        mac_addresses = [
            factory.make_mac_address() for _ in range(3)]
        architecture = make_usable_architecture(self, subarch_name='generic')
        power_type = random.choice(self.power_types)['name']
        power_parameters = dumps({})

        arch, subarch = architecture.split('/')
        node = create_node(
            cluster.uuid, arch, power_type, power_parameters,
            mac_addresses)

        self.assertEqual(architecture, node.architecture)


class TestMarkNodeFailed(MAASServerTestCase):

    def test__marks_node_as_failed(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        mark_node_failed(node.system_id, factory.make_name('error'))
        self.assertEqual(
            NODE_STATUS.FAILED_COMMISSIONING, reload_object(node).status)

    def test__raises_NoSuchNode_if_node_doesnt_exist(self):
        self.assertRaises(
            NoSuchNode,
            mark_node_failed, factory.make_name(), factory.make_name('error'))

    def test__raises_NodeStateViolation_if_wrong_transition(self):
        node = factory.make_Node(status=NODE_STATUS.ALLOCATED)
        self.assertRaises(
            NodeStateViolation,
            mark_node_failed, node.system_id, factory.make_name('error'))


class TestRequestNodeInfoByMACAddress(MAASServerTestCase):

    def test_request_node_info_by_mac_address_raises_exception_no_mac(self):
        self.assertRaises(
            NoSuchNode, request_node_info_by_mac_address,
            factory.make_mac_address())

    def test_request_node_info_by_mac_address_returns_node_for_mac(self):
        mac_address = factory.make_MACAddress_with_Node()
        node, boot_purpose = request_node_info_by_mac_address(
            mac_address.mac_address.get_raw())
        self.assertEqual(node, mac_address.node)

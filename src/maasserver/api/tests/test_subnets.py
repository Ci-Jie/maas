# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for Subnet API."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import httplib
import json
import random

from django.core.urlresolvers import reverse
from maasserver.enum import (
    IPADDRESS_TYPE,
    NODEGROUP_STATUS,
)
from maasserver.testing.api import (
    APITestCase,
    explain_unexpected_response,
)
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import MAASServerTestCase
from provisioningserver.utils.network import inet_ntop
from testtools.matchers import (
    ContainsDict,
    Equals,
)


def get_subnets_uri():
    """Return a Subnet's URI on the API."""
    return reverse('subnets_handler', args=[])


def get_subnet_uri(subnet):
    """Return a Subnet URI on the API."""
    return reverse(
        'subnet_handler', args=[subnet.id])


class TestSubnetsAPI(APITestCase):

    def test_handler_path(self):
        self.assertEqual(
            '/api/1.0/subnets/', get_subnets_uri())

    def test_read(self):
        subnets = [
            factory.make_Subnet()
            for _ in range(3)
        ]
        uri = get_subnets_uri()
        response = self.client.get(uri)

        self.assertEqual(httplib.OK, response.status_code, response.content)
        expected_ids = [
            subnet.id
            for subnet in subnets
            ]
        result_ids = [
            subnet["id"]
            for subnet in json.loads(response.content)
            ]
        self.assertItemsEqual(expected_ids, result_ids)

    def test_create(self):
        self.become_admin()
        subnet_name = factory.make_name("subnet")
        vlan = factory.make_VLAN()
        space = factory.make_Space()
        network = factory.make_ip4_or_6_network()
        cidr = unicode(network.cidr)
        gateway_ip = factory.pick_ip_in_network(network)
        dns_servers = []
        for _ in range(2):
            dns_servers.append(
                factory.pick_ip_in_network(
                    network, but_not=[gateway_ip] + dns_servers))
        uri = get_subnets_uri()
        response = self.client.post(uri, {
            "name": subnet_name,
            "vlan": vlan.id,
            "space": space.id,
            "cidr": cidr,
            "gateway_ip": gateway_ip,
            "dns_servers": ','.join(dns_servers),
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        created_subnet = json.loads(response.content)
        self.assertEqual(subnet_name, created_subnet['name'])
        self.assertEqual(vlan.id, created_subnet['vlan']['id'])
        self.assertEqual(space.name, created_subnet['space'])
        self.assertEqual(cidr, created_subnet['cidr'])
        self.assertEqual(gateway_ip, created_subnet['gateway_ip'])
        self.assertEqual(dns_servers, created_subnet['dns_servers'])

    def test_create_admin_only(self):
        subnet_name = factory.make_name("subnet")
        uri = get_subnets_uri()
        response = self.client.post(uri, {
            "name": subnet_name,
        })
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)

    def test_create_requires_name_vlan_space_cidr(self):
        self.become_admin()
        uri = get_subnets_uri()
        response = self.client.post(uri, {})
        self.assertEqual(
            httplib.BAD_REQUEST, response.status_code, response.content)
        self.assertEqual({
            "cidr": ["This field is required."],
            }, json.loads(response.content))


class TestSubnetAPI(APITestCase):

    def test_handler_path(self):
        subnet = factory.make_Subnet()
        self.assertEqual(
            '/api/1.0/subnets/%s/' % subnet.id,
            get_subnet_uri(subnet))

    def test_read(self):
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.get(uri)

        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_subnet = json.loads(response.content)
        self.assertThat(parsed_subnet, ContainsDict({
            "id": Equals(subnet.id),
            "name": Equals(subnet.name),
            "vlan": ContainsDict({
                "id": Equals(subnet.vlan.id),
                }),
            "space": Equals(subnet.space.name),
            "cidr": Equals(subnet.cidr),
            "gateway_ip": Equals(subnet.gateway_ip),
            "dns_servers": Equals(subnet.dns_servers),
            }))

    def test_read_404_when_bad_id(self):
        uri = reverse(
            'subnet_handler', args=[random.randint(100, 1000)])
        response = self.client.get(uri)
        self.assertEqual(
            httplib.NOT_FOUND, response.status_code, response.content)

    def test_update(self):
        self.become_admin()
        subnet = factory.make_Subnet()
        new_name = factory.make_name("subnet")
        uri = get_subnet_uri(subnet)
        response = self.client.put(uri, {
            "name": new_name,
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        self.assertEqual(new_name, json.loads(response.content)['name'])
        self.assertEqual(new_name, reload_object(subnet).name)

    def test_update_admin_only(self):
        subnet = factory.make_Subnet()
        new_name = factory.make_name("subnet")
        uri = get_subnet_uri(subnet)
        response = self.client.put(uri, {
            "name": new_name,
        })
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)

    def test_delete_deletes_subnet(self):
        self.become_admin()
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.NO_CONTENT, response.status_code, response.content)
        self.assertIsNone(reload_object(subnet))

    def test_delete_403_when_not_admin(self):
        subnet = factory.make_Subnet()
        uri = get_subnet_uri(subnet)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)
        self.assertIsNotNone(reload_object(subnet))

    def test_delete_404_when_invalid_id(self):
        self.become_admin()
        uri = reverse(
            'subnet_handler', args=[random.randint(100, 1000)])
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.NOT_FOUND, response.status_code, response.content)


class TestSubnetAPIAuth(MAASServerTestCase):
    """Authorization tests for subnet API."""
    def test__reserved_ip_ranges_fails_if_not_logged_in(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            httplib.UNAUTHORIZED, response.status_code,
            explain_unexpected_response(httplib.UNAUTHORIZED, response))

    def test__unreserved_ip_ranges_fails_if_not_logged_in(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            httplib.UNAUTHORIZED, response.status_code,
            explain_unexpected_response(httplib.UNAUTHORIZED, response))


class TestSubnetReservedIPRangesAPI(APITestCase):

    def test__returns_empty_list_for_empty_subnet(self):
        subnet = factory.make_Subnet()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            httplib.OK, response.status_code,
            explain_unexpected_response(httplib.OK, response))
        result = json.loads(response.content)
        self.assertThat(result, Equals([]))

    def test__accounts_for_reserved_ip_address(self):
        subnet = factory.make_Subnet()
        ip = factory.pick_ip_in_network(subnet.get_ipnetwork())
        factory.make_StaticIPAddress(
            ip=ip, alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'reserved_ip_ranges'})
        self.assertEqual(
            httplib.OK, response.status_code,
            explain_unexpected_response(httplib.OK, response))
        result = json.loads(response.content)
        self.assertThat(result, Equals([
            {
                "start": ip,
                "end": ip,
                "purpose": ["assigned-ip"],
                "num_addresses": 1,
            }]))


class TestSubnetUnreservedIPRangesAPI(APITestCase):

    def test__returns_full_list_for_empty_subnet(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            httplib.OK, response.status_code,
            explain_unexpected_response(httplib.OK, response))
        result = json.loads(response.content)
        expected_addresses = (network.last - network.first + 1)
        expected_first_address = inet_ntop(network.first + 1)
        if network.version == 6:
            # Don't count the IPv6 network address in num_addresses
            expected_addresses -= 1
            expected_last_address = inet_ntop(network.last)
        else:
            # Don't count the IPv4 broadcast/network addresses in num_addresses
            expected_addresses -= 2
            expected_last_address = inet_ntop(network.last - 1)
        self.assertThat(result, Equals([
            {
                "start": expected_first_address,
                "end": expected_last_address,
                "num_addresses": expected_addresses,
            }]))

    def test__returns_empty_list_for_full_subnet(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        first_address = inet_ntop(network.first + 1)
        range_start = inet_ntop(network.first + 2)
        if network.version == 6:
            last_address = inet_ntop(network.last)
        else:
            last_address = inet_ntop(network.last - 1)
        ng = factory.make_NodeGroup(status=NODEGROUP_STATUS.ENABLED)
        factory.make_NodeGroupInterface(
            ng, ip=first_address, ip_range_low=range_start,
            ip_range_high=last_address, static_ip_range_low='',
            static_ip_range_high='', subnet=subnet)
        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        result = json.loads(response.content)
        self.assertEqual(
            httplib.OK, response.status_code,
            explain_unexpected_response(httplib.OK, response))
        self.assertThat(
            result, Equals([]), unicode(subnet.get_ipranges_in_use()))

    def test__accounts_for_reserved_ip_address(self):
        subnet = factory.make_Subnet()
        network = subnet.get_ipnetwork()
        # Pick an address in the middle of the range. (that way we'll always
        # expect there to be two unreserved ranges, arranged around the
        # allocated IP address.)
        middle_ip = (network.first + network.last) / 2
        ip = inet_ntop(middle_ip)
        factory.make_StaticIPAddress(
            ip=ip, alloc_type=IPADDRESS_TYPE.AUTO, subnet=subnet)

        expected_addresses = (network.last - network.first + 1)
        expected_first_address = inet_ntop(network.first + 1)
        first_range_end = inet_ntop(middle_ip - 1)
        first_range_size = middle_ip - network.first - 1
        second_range_start = inet_ntop(middle_ip + 1)
        if network.version == 6:
            # Don't count the IPv6 network address in num_addresses
            expected_addresses -= 1
            expected_last_address = inet_ntop(network.last)
            second_range_size = network.last - middle_ip
        else:
            # Don't count the IPv4 broadcast/network addresses in num_addresses
            expected_addresses -= 2
            expected_last_address = inet_ntop(network.last - 1)
            second_range_size = network.last - middle_ip - 1

        response = self.client.get(
            get_subnet_uri(subnet),
            {'op': 'unreserved_ip_ranges'})
        self.assertEqual(
            httplib.OK, response.status_code,
            explain_unexpected_response(httplib.OK, response))
        result = json.loads(response.content)
        self.assertThat(result, Equals([
            {
                "start": expected_first_address,
                "end": first_range_end,
                "num_addresses": first_range_size,
            },
            {
                "start": second_range_start,
                "end": expected_last_address,
                "num_addresses": second_range_size,
            }]))
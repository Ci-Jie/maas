# Copyright 2013-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test node constraint forms."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from random import randint

from django import forms
from django.core.exceptions import ValidationError
from maasserver.fields import MAC
from maasserver.models import Node
from maasserver.node_constraint_filter_forms import (
    AcquireNodeForm,
    detect_nonexistent_zone_names,
    generate_architecture_wildcards,
    get_architecture_wildcards,
    get_storage_constraints_from_string,
    JUJU_ACQUIRE_FORM_FIELDS_MAPPING,
    nodes_by_storage,
    parse_legacy_tags,
    RenamableFieldsForm,
    )
from maasserver.testing.architecture import patch_usable_architectures
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils import ignore_unused
from testtools.matchers import ContainsAll


class TestUtils(MAASServerTestCase):

    def test_generate_architecture_wildcards(self):
        # Create a test architecture choice list of one architecture that only
        # has one available subarch (single_subarch) and two architectures that
        # have a matching primary architecture (double_subarch_{1,2})
        single_subarch = factory.make_name('arch'), factory.make_name('arch')
        double_subarch_1 = factory.make_name('arch'), factory.make_name('arch')
        double_subarch_2 = double_subarch_1[0], factory.make_name('arch')
        arches = [
            '/'.join(single_subarch),
            '/'.join(double_subarch_1),
            '/'.join(double_subarch_2),
            ]

        # single_subarch should end up in the dict essentially unchanged, and
        # the double_subarchs should have been flattened into a single dict
        # element with a list of them.
        self.assertEquals(
            {
                single_subarch[0]: frozenset([arches[0]]),
                double_subarch_1[0]: frozenset([arches[1], arches[2]]),
            },
            generate_architecture_wildcards(arches)
        )

    def test_get_architecture_wildcards_aliases_armhf_as_arm(self):
        subarch = factory.make_name('sub')
        arches = ['armhf/%s' % subarch]
        self.assertEqual(
            {
                'arm': frozenset(arches),
                'armhf': frozenset(arches),
            },
            get_architecture_wildcards(arches))

    def test_get_architecture_wildcards_does_not_overwrite_existing_arm(self):
        arm = 'arm/%s' % factory.make_name('armsub')
        armhf = 'armhf/%s' % factory.make_name('armhfsub')
        self.assertEqual(
            {
                'arm': frozenset([arm]),
                'armhf': frozenset([armhf]),
            },
            get_architecture_wildcards([arm, armhf]))

    def test_parse_legacy_tags(self):
        self.assertEquals([], parse_legacy_tags([]))
        self.assertEquals(['a', 'b'], parse_legacy_tags(['a', 'b']))
        self.assertEquals(['a', 'b'], parse_legacy_tags(['a b']))
        self.assertEquals(['a', 'b'], parse_legacy_tags(['a, b']))
        self.assertEquals(['a', 'b', 'c'], parse_legacy_tags(['a, b c']))
        self.assertEquals(['a', 'b'], parse_legacy_tags(['a,b']))
        self.assertEquals(
            ['a', 'b', 'c', 'd'], parse_legacy_tags(['a,b', 'c d']))

    def test_JUJU_ACQUIRE_FORM_FIELDS_MAPPING_fields(self):
        self.assertThat(
            list(AcquireNodeForm().fields),
            ContainsAll(JUJU_ACQUIRE_FORM_FIELDS_MAPPING))

    def test_detect_nonexistent_zone_names_returns_empty_if_no_names(self):
        self.assertEqual([], detect_nonexistent_zone_names([]))

    def test_detect_nonexistent_zone_names_returns_empty_if_all_OK(self):
        zones = [factory.make_Zone() for _ in range(3)]
        self.assertEqual(
            [],
            detect_nonexistent_zone_names([zone.name for zone in zones]))

    def test_detect_nonexistent_zone_names_reports_unknown_zone_names(self):
        non_zone = factory.make_name('nonzone')
        self.assertEqual([non_zone], detect_nonexistent_zone_names([non_zone]))

    def test_detect_nonexistent_zone_names_is_consistent(self):
        names = [factory.make_name('nonzone') for _ in range(3)]
        self.assertEqual(
            detect_nonexistent_zone_names(names),
            detect_nonexistent_zone_names(names))

    def test_detect_nonexistent_zone_names_combines_good_and_bad_names(self):
        zone = factory.make_Zone().name
        non_zone = factory.make_name('nonzone')
        self.assertEqual(
            [non_zone],
            detect_nonexistent_zone_names([zone, non_zone]))

    def test_detect_nonexistent_zone_names_asserts_parameter_type(self):
        self.assertRaises(
            AssertionError, detect_nonexistent_zone_names, "text")

    def test_get_storage_constraints_from_string_returns_None_for_empty(self):
        self.assertEquals(None, get_storage_constraints_from_string(""))

    def test_get_storage_constraints_from_string_None_for_empty_tags(self):
        self.assertEquals(
            [None, None, None],
            [tags for _, tags in get_storage_constraints_from_string("0,0,0")])

    def test_get_storage_constraints_from_string_returns_size_in_bytes(self):
        self.assertEquals(
            [int(1.5 * (1000 ** 3)), 3 * (1000 ** 3), int(6.75 * (1000 ** 3))],
            [
                size
                for size, _ in get_storage_constraints_from_string(
                    "1.5,3,6.75")
            ])

    def test_get_storage_constraints_from_string_sorts_more_tags_first(self):
        self.assertEquals(
            [['ssd', 'sata', 'removable'], ['ssd', 'sata'], ['ssd']],
            [
                tags
                for _, tags in get_storage_constraints_from_string(
                    "0(ssd,sata),0(ssd),0(ssd,sata,removable)")
            ])

    def test_nodes_by_storage_returns_None_when_storage_string_is_empty(self):
        self.assertEquals(None, nodes_by_storage(""))


class TestRenamableForm(RenamableFieldsForm):
    field1 = forms.CharField(label="A field which is forced to contain 'foo'.")
    field2 = forms.CharField(label="Field 2", required=False)

    def clean_field1(self):
        name = self.get_field_name('field1')
        value = self.cleaned_data[name]
        if value != "foo":
            raise ValidationError("The value should be 'foo'")
        return value


class TestRenamableFieldsForm(MAASServerTestCase):

    def test_rename_field_renames_field(self):
        form = TestRenamableForm()
        form.rename_field('field1', 'new_field')
        self.assertItemsEqual(form.fields, ['new_field', 'field2'])

    def test_rename_field_updates_mapping(self):
        form = TestRenamableForm()
        form.rename_field('field1', 'new_field')
        self.assertEquals('new_field', form.get_field_name('field1'))

    def test_rename_field_renames_validation_method(self):
        form = TestRenamableForm(data={'new_field': 'not foo', 'field2': 'a'})
        form.rename_field('field1', 'new_field')
        self.assertEquals(
            (False, {'new_field': ["The value should be 'foo'"]}),
            (form.is_valid(), form.errors))


class TestAcquireNodeForm(MAASServerTestCase):

    def set_usable_arch(self):
        """Produce an arbitrary, valid, architecture name."""
        arch = '%s/%s' % (factory.make_name('arch'), factory.make_name('sub'))
        patch_usable_architectures(self, [arch])
        return arch

    def test_strict_form_checks_unknown_constraints(self):
        data = {'unknown_constraint': 'boo'}
        form = AcquireNodeForm.Strict(data=data)
        self.assertEquals(
            (False, {'unknown_constraint': ["No such constraint."]}),
            (form.is_valid(), form.errors))

    def test_not_strict_does_not_check_unknown_constraints(self):
        data = {'unknown_constraint': 'boo'}
        form = AcquireNodeForm(data=data)
        self.assertTrue(form.is_valid())

    def assertConstrainedNodes(self, nodes, data):
        form = AcquireNodeForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertItemsEqual(nodes, form.filter_nodes(Node.objects.all()))

    def test_no_constraints(self):
        nodes = [factory.make_Node() for _ in range(3)]
        form = AcquireNodeForm(data={})
        self.assertTrue(form.is_valid())
        self.assertItemsEqual(nodes, Node.objects.all())

    def test_hostname(self):
        nodes = [factory.make_Node() for _ in range(3)]
        self.assertConstrainedNodes([nodes[0]], {'name': nodes[0].hostname})
        self.assertConstrainedNodes([], {'name': 'unknown-name'})

    def test_hostname_with_domain_part(self):
        nodes = [factory.make_Node() for _ in range(3)]
        self.assertConstrainedNodes(
            [nodes[0]],
            {'name': '%s.%s' % (nodes[0].hostname, nodes[0].nodegroup.name)})
        self.assertConstrainedNodes(
            [],
            {'name': '%s.%s' % (nodes[0].hostname, 'unknown-domain')})
        self.assertConstrainedNodes(
            [],
            {'name': '%s.%s' % (nodes[0].hostname, nodes[1].nodegroup.name)})
        node = factory.make_Node(hostname="host21.mydomain")
        self.assertConstrainedNodes(
            [node],
            {'name': 'host21.mydomain'})

        self.assertConstrainedNodes(
            [node],
            {'name': 'host21.%s' % node.nodegroup.name})

    def test_cpu_count(self):
        node1 = factory.make_Node(cpu_count=1)
        node2 = factory.make_Node(cpu_count=2)
        nodes = [node1, node2]
        self.assertConstrainedNodes(nodes, {'cpu_count': '0'})
        self.assertConstrainedNodes(nodes, {'cpu_count': '1.0'})
        self.assertConstrainedNodes([node2], {'cpu_count': '2'})
        self.assertConstrainedNodes([], {'cpu_count': '4'})

    def test_invalid_cpu_count(self):
        form = AcquireNodeForm(data={'cpu_count': 'invalid'})
        self.assertEquals(
            (False, {'cpu_count': ["Invalid CPU count: number required."]}),
            (form.is_valid(), form.errors))

    def test_memory(self):
        node1 = factory.make_Node(memory=1024)
        node2 = factory.make_Node(memory=4096)
        self.assertConstrainedNodes([node1, node2], {'mem': '512'})
        self.assertConstrainedNodes([node1, node2], {'mem': '1024'})
        self.assertConstrainedNodes([node2], {'mem': '2048'})
        self.assertConstrainedNodes([node2], {'mem': '4096'})
        self.assertConstrainedNodes([], {'mem': '8192'})
        self.assertConstrainedNodes([node2], {'mem': '4096.0'})

    def test_invalid_memory(self):
        form = AcquireNodeForm(data={'mem': 'invalid'})
        self.assertEquals(
            (False, {'mem': ["Invalid memory: number of MiB required."]}),
            (form.is_valid(), form.errors))

    def test_networks_filters_by_name(self):
        networks = factory.make_Networks(5)
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        # Filter for this network.  Take one in the middle to avoid
        # coincidental success based on ordering.
        pick = 2
        self.assertConstrainedNodes(
            {macs[pick].node},
            {'networks': [networks[pick].name]})

    def test_networks_filters_by_ip(self):
        networks = factory.make_Networks(5)
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        # Filter for this network.  Take one in the middle to avoid
        # coincidental success based on ordering.
        pick = 2
        self.assertConstrainedNodes(
            {macs[pick].node},
            {'networks': ['ip:%s' % networks[pick].ip]})

    def test_networks_filters_by_vlan_tag(self):
        vlan_tags = list(range(5))
        networks = [factory.make_Network(vlan_tag=tag) for tag in vlan_tags]
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        # Filter for this network.  Take one in the middle to avoid
        # coincidental success based on ordering.
        pick = 2
        self.assertConstrainedNodes(
            {macs[pick].node},
            {'networks': ['vlan:%d' % vlan_tags[pick]]})

    def test_networks_filter_ignores_macs_on_other_networks(self):
        network = factory.make_Network()
        node = factory.make_Node()
        factory.make_MACAddress(node=node, networks=[network])
        factory.make_MACAddress(node=node, networks=[factory.make_Network()])
        self.assertConstrainedNodes({node}, {'networks': [network.name]})

    def test_networks_filter_ignores_other_networks_on_mac(self):
        networks = factory.make_Networks(3)
        mac = factory.make_MACAddress_with_Node(networks=networks)
        self.assertConstrainedNodes(
            {mac.node},
            {'networks': [networks[1].name]})

    def test_invalid_networks(self):
        form = AcquireNodeForm(data={'networks': 'ip:10.0.0.0'})
        self.assertEquals(
            (
                False,
                {
                    'networks': [
                        "Invalid parameter: list of networks required.",
                        ],
                },
            ),
            (form.is_valid(), form.errors))

        # The validator is unit-tested separately.  This just verifies that it
        # is being consulted.
        form = AcquireNodeForm(data={'networks': ['vlan:-1']})
        self.assertEquals(
            (False, {'networks': ["VLAN tag out of range (1-4094)."]}),
            (form.is_valid(), form.errors))

    def test_networks_combines_filters(self):
        networks = factory.make_Networks(3)
        [
            network_by_name,
            network_by_ip,
            network_by_vlan,
        ] = networks
        if network_by_vlan.vlan_tag is None:
            # For this test we need network_by_vlan to be a VLAN.
            network_by_vlan.vlan_tag = factory.make_vlan_tag(
                but_not=[network.vlan_tag for network in networks])
            network_by_vlan.save()

        factory.make_MACAddress_with_Node(
            networks=[network_by_name, network_by_ip])
        factory.make_MACAddress_with_Node(
            networks=[network_by_name, network_by_vlan])
        right_mac = factory.make_MACAddress_with_Node(
            networks=[network_by_name, network_by_ip, network_by_vlan])
        factory.make_MACAddress_with_Node(
            networks=[network_by_ip, network_by_vlan])
        factory.make_MACAddress_with_Node(networks=[])

        self.assertConstrainedNodes(
            {right_mac.node},
            {
                'networks': [
                    network_by_name.name,
                    'ip:%s' % network_by_ip.ip,
                    'vlan:%d' % network_by_vlan.vlan_tag,
                    ],
            })

    def test_networks_ignores_other_networks(self):
        [this_network, other_network] = factory.make_Networks(2)
        mac = factory.make_MACAddress_with_Node(
            networks=[this_network, other_network])
        self.assertConstrainedNodes(
            [mac.node],
            {'networks': [this_network.name]})

    def test_not_networks_filters_by_name(self):
        networks = factory.make_Networks(2)
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        self.assertConstrainedNodes(
            {macs[0].node},
            {'not_networks': [networks[1].name]})

    def test_not_networks_filters_by_ip(self):
        networks = factory.make_Networks(2)
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        self.assertConstrainedNodes(
            {macs[0].node},
            {'not_networks': ['ip:%s' % networks[1].ip]})

    def test_not_networks_filters_by_vlan_tag(self):
        vlan_tags = range(2)
        networks = [factory.make_Network(vlan_tag=tag) for tag in vlan_tags]
        macs = [
            factory.make_MACAddress_with_Node(networks=[network])
            for network in networks
            ]
        self.assertConstrainedNodes(
            {macs[0].node},
            {'not_networks': ['vlan:%d' % vlan_tags[1]]})

    def test_not_networks_accepts_nodes_without_network_connections(self):
        macless_node = factory.make_Node()
        unconnected_mac = factory.make_MACAddress_with_Node(networks=[])
        self.assertConstrainedNodes(
            {macless_node, unconnected_mac.node},
            {'not_networks': [factory.make_Network().name]})

    def test_not_networks_excludes_node_with_any_mac_on_not_networks(self):
        network = factory.make_Network()
        node = factory.make_Node()
        factory.make_MACAddress(node=node, networks=[network])
        factory.make_MACAddress(node=node, networks=[factory.make_Network()])
        self.assertConstrainedNodes([], {'not_networks': [network.name]})

    def test_not_networks_excludes_node_with_mac_on_any_not_networks(self):
        networks = factory.make_Networks(3)
        not_network = networks[1]
        factory.make_MACAddress_with_Node(networks=[not_network])
        self.assertConstrainedNodes([], {'not_networks': [not_network.name]})

    def test_invalid_not_networks(self):
        form = AcquireNodeForm(data={'not_networks': 'ip:10.0.0.0'})
        self.assertEquals(
            (
                False,
                {
                    'not_networks': [
                        "Invalid parameter: list of networks required.",
                        ],
                },
            ),
            (form.is_valid(), form.errors))

        # The validator is unit-tested separately.  This just verifies that it
        # is being consulted.
        form = AcquireNodeForm(data={'not_networks': ['vlan:-1']})
        self.assertEquals(
            (False, {'not_networks': ["VLAN tag out of range (1-4094)."]}),
            (form.is_valid(), form.errors))

    def test_not_networks_combines_filters(self):
        networks = factory.make_Networks(5)
        [
            network_by_name,
            network_by_ip,
            network_by_vlan,
            other_network,
            remaining_network,
        ] = networks
        if network_by_vlan.vlan_tag is None:
            # For this test we need network_by_vlan to be a VLAN.
            network_by_vlan.vlan_tag = factory.make_vlan_tag(
                but_not=[network.vlan_tag for network in networks])
            network_by_vlan.save()

        factory.make_MACAddress_with_Node(networks=[network_by_name])
        factory.make_MACAddress_with_Node(
            networks=[network_by_name, network_by_ip])
        factory.make_MACAddress_with_Node(
            networks=[network_by_name, network_by_vlan])
        factory.make_MACAddress_with_Node(networks=[network_by_vlan])
        factory.make_MACAddress_with_Node(
            networks=[network_by_vlan, other_network])
        right_mac = factory.make_MACAddress_with_Node(
            networks=[remaining_network])

        self.assertConstrainedNodes(
            {right_mac.node},
            {
                'not_networks': [
                    network_by_name.name,
                    'ip:%s' % network_by_ip.ip,
                    'vlan:%d' % network_by_vlan.vlan_tag,
                    ],
            })

    def test_connected_to(self):
        mac1 = MAC('aa:bb:cc:dd:ee:ff')
        mac2 = MAC('00:11:22:33:44:55')
        node1 = factory.make_Node(routers=[mac1, mac2])
        node2 = factory.make_Node(routers=[mac1])
        factory.make_Node()
        self.assertConstrainedNodes(
            [node1], {'connected_to': [
                mac1.get_raw(), mac2.get_raw()]})
        self.assertConstrainedNodes(
            [node1, node2], {'connected_to': [mac1.get_raw()]})

    def test_invalid_connected_to(self):
        form = AcquireNodeForm(data={'connected_to': 'invalid'})
        self.assertEquals(
            (False, {
                'connected_to':
                ["Invalid parameter: list of MAC addresses required."]}),
            (form.is_valid(), form.errors))

    def test_not_connected_to(self):
        mac1 = MAC('aa:bb:cc:dd:ee:ff')
        mac2 = MAC('00:11:22:33:44:55')
        node1 = factory.make_Node(routers=[mac1, mac2])
        node2 = factory.make_Node(routers=[mac1])
        node3 = factory.make_Node()
        self.assertConstrainedNodes(
            [node3], {'not_connected_to': [
                mac1.get_raw(), mac2.get_raw()]})
        self.assertConstrainedNodes(
            [node2, node3], {'not_connected_to': [mac2.get_raw()]})
        self.assertConstrainedNodes(
            [node1, node2, node3], {'not_connected_to': ["b1:b1:b1:b1:b1:b1"]})

    def test_invalid_not_connected_to(self):
        form = AcquireNodeForm(data={'not_connected_to': 'invalid'})
        self.assertEquals(
            (False, {
                'not_connected_to':
                ["Invalid parameter: list of MAC addresses required."]}),
            (form.is_valid(), form.errors))

    def test_zone(self):
        node1 = factory.make_Node()
        node2 = factory.make_Node()
        node3 = factory.make_Node()
        zone1 = factory.make_Zone(nodes=[node1, node2])
        zone2 = factory.make_Zone()

        self.assertConstrainedNodes(
            [node1, node2], {'zone': zone1.name})
        self.assertConstrainedNodes(
            [node1, node2, node3], {'zone': ''})
        self.assertConstrainedNodes(
            [node1, node2, node3], {})
        self.assertConstrainedNodes(
            [], {'zone': zone2.name})

    def test_invalid_zone(self):
        form = AcquireNodeForm(data={'zone': 'unknown'})
        self.assertEquals(
            (False, {'zone': ["No such zone: 'unknown'."]}),
            (form.is_valid(), form.errors))

    def test_not_in_zone_excludes_given_zones(self):
        ineligible_nodes = [factory.make_Node() for _ in range(2)]
        eligible_nodes = [factory.make_Node() for _ in range(2)]
        self.assertConstrainedNodes(
            eligible_nodes,
            {'not_in_zone': [node.zone.name for node in ineligible_nodes]})

    def test_not_in_zone_with_required_zone_yields_no_nodes(self):
        zone = factory.make_Zone()
        factory.make_Node(zone=zone)
        self.assertConstrainedNodes([], {'zone': zone, 'not_in_zone': [zone]})

    def test_validates_not_in_zone(self):
        bad_zone_name = '#$&*!'
        form = AcquireNodeForm(data={'not_in_zone': [bad_zone_name]})
        self.assertFalse(form.is_valid())
        self.assertEqual(['not_in_zone'], form.errors.keys())

    def test_not_in_zone_must_be_zone_name(self):
        non_zone = factory.make_name('nonzone')
        form = AcquireNodeForm(data={'not_in_zone': [non_zone]})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            {'not_in_zone': ["No such zone(s): %s." % non_zone]},
            form.errors)

    def test_not_in_zone_can_exclude_multiple_zones(self):
        # Three nodes, all in different physical zones.  If we say we don't
        # want the first node's zone or the second node's zone, we get the node
        # in the remaining zone.
        nodes = [factory.make_Node() for _ in range(3)]
        self.assertConstrainedNodes(
            [nodes[2]],
            {'not_in_zone': [nodes[0].zone.name, nodes[1].zone.name]})

    def test_tags(self):
        tag_big = factory.make_Tag(name='big')
        tag_burly = factory.make_Tag(name='burly')
        node_big = factory.make_Node()
        node_big.tags.add(tag_big)
        node_burly = factory.make_Node()
        node_burly.tags.add(tag_burly)
        node_bignburly = factory.make_Node()
        node_bignburly.tags.add(tag_big)
        node_bignburly.tags.add(tag_burly)
        self.assertConstrainedNodes(
            [node_big, node_bignburly], {'tags': ['big']})
        self.assertConstrainedNodes(
            [node_burly, node_bignburly], {'tags': ['burly']})
        self.assertConstrainedNodes(
            [node_bignburly], {'tags': ['big', 'burly']})

    def test_not_tags_negates_individual_tags(self):
        tag = factory.make_Tag()
        tagged_node = factory.make_Node()
        tagged_node.tags.add(tag)
        untagged_node = factory.make_Node()

        self.assertConstrainedNodes(
            [untagged_node], {'not_tags': [tag.name]})

    def test_not_tags_negates_multiple_tags(self):
        tagged_node = factory.make_Node()
        tags = [
            factory.make_Tag('spam'),
            factory.make_Tag('eggs'),
            factory.make_Tag('ham'),
            ]
        tagged_node.tags = tags
        partially_tagged_node = factory.make_Node()
        partially_tagged_node.tags.add(tags[0])

        self.assertConstrainedNodes(
            [partially_tagged_node],
            {'not_tags': ['eggs', 'ham']})

    def test_invalid_tags(self):
        form = AcquireNodeForm(data={'tags': ['big', 'unknown']})
        self.assertEquals(
            (False, {
                'tags':
                ["No such tag(s): 'big', 'unknown'."]}),
            (form.is_valid(), form.errors))

    def test_storage_invalid_constraint(self):
        form = AcquireNodeForm(data={'storage': '10(ssd,20'})
        self.assertEquals(
            (False, {
                'storage':
                ["Malformed storage contraint, size must be numeric. "
                 "Recieved 'ssd' instead."]}),
            (form.is_valid(), form.errors))

    def test_storage_invalid_size_constraint(self):
        form = AcquireNodeForm(data={'storage': 'abc'})
        self.assertEquals(
            (False, {
                'storage':
                ["Malformed storage contraint, size must be numeric. "
                 "Recieved 'abc' instead."]}),
            (form.is_valid(), form.errors))

    def test_storage_single_contraint_only_matches_physical_devices(self):
        node1 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node1)
        node2 = factory.make_Node()
        factory.make_BlockDevice(node=node2)
        self.assertConstrainedNodes([node1], {'storage': '0'})

    def test_storage_single_contraint_matches_all_sizes_larger(self):
        node1 = factory.make_Node()
        # 1gb block device
        factory.make_PhysicalBlockDevice(
            node=node1, size=1 * (1000 ** 3))
        node2 = factory.make_Node()
        # 4gb block device
        factory.make_PhysicalBlockDevice(
            node=node2, size=4 * (1000 ** 3))
        node3 = factory.make_Node()
        # 8gb block device
        factory.make_PhysicalBlockDevice(
            node=node3, size=8 * (1000 ** 3))
        # all nodes with physical devices larger than 2gb
        self.assertConstrainedNodes([node2, node3], {'storage': '2'})

    def test_storage_single_contraint_matches_on_tags(self):
        node1 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node1, tags=['ssd'])
        node2 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node2, tags=['rotary'])
        self.assertConstrainedNodes([node1], {'storage': '0(ssd)'})

    def test_storage_single_contraint_matches_decimal_size(self):
        node1 = factory.make_Node()
        # 1gb, 2gb block device
        factory.make_PhysicalBlockDevice(
            node=node1, size=1 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node1, size=2 * (1000 ** 3))
        node2 = factory.make_Node()
        # 1gb block device
        factory.make_PhysicalBlockDevice(
            node=node2, size=1 * (1000 ** 3))
        self.assertConstrainedNodes([node1], {'storage': '1.5'})

    def test_storage_multi_contraint_only_matches_physical_devices(self):
        node1 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node1)
        factory.make_PhysicalBlockDevice(node=node1)
        node2 = factory.make_Node()
        factory.make_BlockDevice(node=node2)
        factory.make_BlockDevice(node=node2)
        self.assertConstrainedNodes([node1], {'storage': '0,0'})

    def test_storage_multi_contraint_matches_all_sizes_larger(self):
        node1 = factory.make_Node()
        # 1gb, 2gb, 3gb block device
        factory.make_PhysicalBlockDevice(
            node=node1, size=1 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node1, size=2 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node1, size=3 * (1000 ** 3))
        node2 = factory.make_Node()
        # 5gb, 6gb, 7gb block device
        factory.make_PhysicalBlockDevice(
            node=node2, size=5 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node2, size=6 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node2, size=7 * (1000 ** 3))
        node3 = factory.make_Node()
        # 8gb, 9gb, 10gb block device
        factory.make_PhysicalBlockDevice(
            node=node3, size=8 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node3, size=9 * (1000 ** 3))
        factory.make_PhysicalBlockDevice(
            node=node3, size=10 * (1000 ** 3))
        # all nodes with physical devices larger than 2gb
        self.assertConstrainedNodes([node2, node3], {'storage': '4,4,4'})

    def test_storage_multi_contraint_matches_on_tags(self):
        node1 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node1, tags=['ssd'])
        factory.make_PhysicalBlockDevice(node=node1, tags=['ssd', 'removable'])
        node2 = factory.make_Node()
        factory.make_PhysicalBlockDevice(node=node2, tags=['ssd'])
        factory.make_PhysicalBlockDevice(node=node2, tags=['ssd', 'sata'])
        self.assertConstrainedNodes(
            [node1], {'storage': '0(ssd),0(ssd,removable)'})

    def test_storage_multi_contraint_matches_on_size_and_tags(self):
        node1 = factory.make_Node()
        # 1gb, 2gb block device
        factory.make_PhysicalBlockDevice(
            node=node1, size=1 * (1000 ** 3),
            tags=['ssd'])
        factory.make_PhysicalBlockDevice(
            node=node1, size=2 * (1000 ** 3),
            tags=['ssd'])
        node2 = factory.make_Node()
        # 4gb, 5gb block device
        factory.make_PhysicalBlockDevice(
            node=node2, size=4 * (1000 ** 3),
            tags=['ssd'])
        factory.make_PhysicalBlockDevice(
            node=node2, size=5 * (1000 ** 3),
            tags=['ssd'])
        self.assertConstrainedNodes(
            [node2], {'storage': '3(ssd),3(ssd)'})

    def test_storage_multi_contraint_matches_large_disk_count(self):
        node1 = factory.make_Node()
        for _ in range(10):
            factory.make_PhysicalBlockDevice(node=node1)
        node2 = factory.make_Node()
        for _ in range(5):
            factory.make_PhysicalBlockDevice(node=node2)
        self.assertConstrainedNodes(
            [node1], {'storage': '0,0,0,0,0,0,0,0,0,0'})

    def test_combined_constraints(self):
        tag_big = factory.make_Tag(name='big')
        arch = '%s/generic' % factory.make_name('arch')
        wrong_arch = '%s/generic' % factory.make_name('arch')
        patch_usable_architectures(self, [arch, wrong_arch])
        node_big = factory.make_Node(architecture=arch)
        node_big.tags.add(tag_big)
        node_small = factory.make_Node(architecture=arch)
        ignore_unused(node_small)
        node_big_other_arch = factory.make_Node(architecture=wrong_arch)
        node_big_other_arch.tags.add(tag_big)
        self.assertConstrainedNodes(
            [node_big, node_big_other_arch], {'tags': ['big']})
        self.assertConstrainedNodes(
            [node_big], {'arch': arch, 'tags': ['big']})

    def test_invalid_combined_constraints(self):
        form = AcquireNodeForm(
            data={'tags': ['unknown'], 'mem': 'invalid'})
        self.assertEquals(
            (False, {
                'tags': ["No such tag(s): 'unknown'."],
                'mem': ["Invalid memory: number of MiB required."],
            }),
            (form.is_valid(), form.errors))

    def test_returns_distinct_nodes(self):
        network = factory.make_Network()
        node = factory.make_Node()
        # Create multiple NICs for `node` connected to `network`.
        [
            factory.make_MACAddress(node=node, networks=[network])
            for _ in range(3)
            ]
        self.assertConstrainedNodes(
            {node},
            {'networks': [network.name]})

    def test_describe_constraints_returns_empty_if_no_constraints(self):
        form = AcquireNodeForm(data={})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual('', form.describe_constraints())

    def test_describe_constraints_shows_simple_constraint(self):
        hostname = factory.make_name('host')
        form = AcquireNodeForm(data={'name': hostname})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual('name=%s' % hostname, form.describe_constraints())

    def test_describe_constraints_shows_arch_as_special_case(self):
        # The "arch" field is technically a single-valued string field
        # on the form, but its "cleaning" produces a list of strings.
        arch = self.set_usable_arch()
        form = AcquireNodeForm(data={'arch': arch})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual('arch=%s' % arch, form.describe_constraints())

    def test_describe_constraints_shows_multi_constraint(self):
        tag = factory.make_Tag()
        form = AcquireNodeForm(data={'tags': [tag.name]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual('tags=%s' % tag.name, form.describe_constraints())

    def test_describe_constraints_sorts_constraints(self):
        hostname = factory.make_name('host')
        zone = factory.make_Zone()
        form = AcquireNodeForm(data={'name': hostname, 'zone': zone})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            'name=%s zone=%s' % (hostname, zone),
            form.describe_constraints())

    def test_describe_constraints_combines_constraint_values(self):
        tag1 = factory.make_Tag()
        tag2 = factory.make_Tag()
        form = AcquireNodeForm(data={'tags': [tag1.name, tag2.name]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            'tags=%s,%s' % tuple(sorted([tag1.name, tag2.name])),
            form.describe_constraints())

    def test_describe_constraints_shows_all_constraints(self):
        constraints = {
            'name': factory.make_name('host'),
            'arch': self.set_usable_arch(),
            'cpu_count': randint(1, 32),
            'mem': randint(1024, 256 * 1024),
            'tags': [factory.make_Tag().name],
            'not_tags': [factory.make_Tag().name],
            'networks': [factory.make_Network().name],
            'not_networks': [factory.make_Network().name],
            'connected_to': [factory.make_mac_address()],
            'not_connected_to': [factory.make_mac_address()],
            'zone': factory.make_Zone(),
            'not_in_zone': [factory.make_Zone().name],
            'storage': '0(ssd),10(ssd)',
            }
        form = AcquireNodeForm(data=constraints)
        self.assertTrue(form.is_valid(), form.errors)
        # Check first: we didn't forget to test any attributes.  When we add
        # a constraint to the form, we'll have to add it here as well.
        self.assertItemsEqual(form.fields.keys(), constraints.keys())

        described_constraints = {
            constraint.split('=', 1)[0]
            for constraint in form.describe_constraints().split()
            }
        self.assertItemsEqual(constraints.keys(), described_constraints)

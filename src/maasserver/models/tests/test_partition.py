# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `Partition`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import random
from uuid import uuid4

from django.core.exceptions import ValidationError
from maasserver.enum import (
    FILESYSTEM_GROUP_TYPE,
    FILESYSTEM_TYPE,
    PARTITION_TABLE_TYPE,
)
from maasserver.models.blockdevice import MIN_BLOCK_DEVICE_SIZE
from maasserver.models.filesystemgroup import VolumeGroup
from maasserver.models.partition import (
    MIN_PARTITION_SIZE,
    Partition,
)
from maasserver.models.partitiontable import PARTITION_TABLE_EXTRA_SPACE
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import MAASServerTestCase
from testtools.matchers import Equals


class TestPartitionManager(MAASServerTestCase):
    """Tests for the `PartitionManager`."""

    def test_get_free_partitions_for_node(self):
        node = factory.make_Node()
        block_device = factory.make_PhysicalBlockDevice(
            node=node,
            size=(MIN_BLOCK_DEVICE_SIZE * 4) + PARTITION_TABLE_EXTRA_SPACE)
        partition_table = factory.make_PartitionTable(
            block_device=block_device)
        free_partitions = [
            partition_table.add_partition(size=MIN_BLOCK_DEVICE_SIZE)
            for _ in range(2)
        ]
        # Make used partitions.
        for _ in range(2):
            factory.make_Filesystem(
                partition=partition_table.add_partition(
                    size=MIN_BLOCK_DEVICE_SIZE))
        self.assertItemsEqual(
            free_partitions,
            Partition.objects.get_free_partitions_for_node(node))

    def test_get_partitions_in_filesystem_group(self):
        node = factory.make_Node()
        filesystem_group = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG)
        block_device_with_partitions = factory.make_PhysicalBlockDevice(
            node=node)
        partition_table = factory.make_PartitionTable(
            block_device=block_device_with_partitions)
        partition = factory.make_Partition(partition_table=partition_table)
        factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.LVM_PV,
            partition=partition, filesystem_group=filesystem_group)
        partitions_in_filesystem_group = (
            Partition.objects.get_partitions_in_filesystem_group(
                filesystem_group))
        self.assertItemsEqual(
            [partition], partitions_in_filesystem_group)

    def test_get_partition_by_id_or_name_returns_valid_with_id(self):
        partition = factory.make_Partition()
        self.assertEquals(
            partition,
            Partition.objects.get_partition_by_id_or_name(partition.id))

    def test_get_partition_by_id_or_name_returns_valid_with_name(self):
        partition = factory.make_Partition()
        self.assertEquals(
            partition,
            Partition.objects.get_partition_by_id_or_name(partition.name))

    def test_get_partition_by_id_or_name_invalid_id(self):
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name,
            random.randint(1000, 5000))

    def test_get_partition_by_id_or_name_empty_string(self):
        factory.make_Partition()
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name, "")

    def test_get_partition_by_id_or_name_invalid_part_seperator(self):
        partition = factory.make_Partition()
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name,
            "%spart%s" % (
                partition.partition_table.block_device.get_name(),
                partition.get_partition_number()
                ))

    def test_get_partition_by_id_or_name_invalid_part_number(self):
        partition = factory.make_Partition()
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name,
            "%spartX" % (
                partition.partition_table.block_device.get_name()))

    def test_get_partition_by_id_or_name_by_id_invalid_table(self):
        partition_table = factory.make_PartitionTable()
        other_table = factory.make_PartitionTable()
        partition = factory.make_Partition(partition_table=partition_table)
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name,
            partition.id, other_table)

    def test_get_partition_by_id_or_name_by_name_invalid_table(self):
        partition_table = factory.make_PartitionTable()
        other_table = factory.make_PartitionTable()
        partition = factory.make_Partition(partition_table=partition_table)
        self.assertRaises(
            Partition.DoesNotExist,
            Partition.objects.get_partition_by_id_or_name,
            partition.name, other_table)

    def test_get_partition_by_device_name_and_number(self):
        block_device = factory.make_PhysicalBlockDevice()
        partition_table = factory.make_PartitionTable(
            block_device=block_device)
        factory.make_Partition(partition_table=partition_table)
        partition_two = factory.make_Partition(partition_table=partition_table)
        self.assertEquals(
            partition_two,
            Partition.objects.get_partition_by_device_name_and_number(
                block_device.get_name(), partition_two.get_partition_number()))


class TestPartition(MAASServerTestCase):
    """Tests for the `Partition` model."""

    def test_name(self):
        block_device_name = factory.make_name("bd")
        block_device = factory.make_PhysicalBlockDevice(name=block_device_name)
        table = factory.make_PartitionTable(block_device=block_device)
        partition = factory.make_Partition(partition_table=table)
        self.assertEquals("%s-part1" % block_device_name, partition.name)

    def test_path(self):
        block_device = factory.make_PhysicalBlockDevice()
        table = factory.make_PartitionTable(block_device=block_device)
        partition = factory.make_Partition(partition_table=table)
        self.assertEquals("%s-part1" % block_device.path, partition.path)

    def test_get_name(self):
        block_device_name = factory.make_name("bd")
        block_device = factory.make_PhysicalBlockDevice(name=block_device_name)
        table = factory.make_PartitionTable(block_device=block_device)
        partition = factory.make_Partition(partition_table=table)
        self.assertEquals("%s-part1" % block_device_name, partition.get_name())

    def test_get_node_returns_partition_table_node(self):
        partition = factory.make_Partition()
        self.assertEquals(
            partition.partition_table.get_node(), partition.get_node())

    def test_get_block_size_returns_partition_table_block_size(self):
        partition = factory.make_Partition()
        self.assertEquals(
            partition.partition_table.get_block_size(),
            partition.get_block_size())

    def test_set_uuid_if_missing(self):
        table = factory.make_PartitionTable()
        partition = factory.make_Partition(partition_table=table)
        self.assertIsNotNone(partition.uuid)

    def test_save_doesnt_overwrite_uuid(self):
        uuid = uuid4()
        table = factory.make_PartitionTable(
            table_type=PARTITION_TABLE_TYPE.GPT)
        partition = factory.make_Partition(partition_table=table, uuid=uuid)
        partition.save()
        self.assertEquals('%s' % uuid, partition.uuid)

    def test_size_is_rounded_to_next_block(self):
        partition = factory.make_Partition()
        partition.size = partition.get_block_size() * 4096
        partition.size += 1
        partition.save()
        self.assertEquals(4097, partition.size / partition.get_block_size())

    def test_validate_enough_space_for_new_partition(self):
        partition_table = factory.make_PartitionTable()
        partition_table.add_partition()
        error = self.assertRaises(
            ValidationError, factory.make_Partition,
            partition_table=partition_table, size=MIN_PARTITION_SIZE)
        self.assertEquals({
            "size": [
                "Partition cannot be saved; not enough free space "
                "on the block device."],
            }, error.error_dict)

    def test_validate_enough_space_for_resize_partition(self):
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        partition.size += partition_table.get_block_size() * 2
        error = self.assertRaises(ValidationError, partition.save)
        self.assertEquals({
            "size": [
                "Partition %s cannot be resized to fit on the "
                "block device; not enough free space." % partition.id],
            }, error.error_dict)

    def test_validate_enough_space_will_round_down_a_block(self):
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        prev_size = partition.size
        partition.size += partition_table.get_block_size()
        partition.save()
        self.assertEquals(prev_size, partition.size)

    def test_partition_add_filesystem(self):
        """Add a file system to a partition"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        filesystem = partition.add_filesystem(fstype=FILESYSTEM_TYPE.EXT4)
        self.assertEquals(filesystem.partition_id, partition.id)
        self.assertEquals(filesystem.fstype, FILESYSTEM_TYPE.EXT4)

    def test_partition_add_second_filesystem(self):
        """Adding a second file system to a partition should fail"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        partition.add_filesystem(fstype=FILESYSTEM_TYPE.EXT4)

        # Adding a second one should fail
        self.assertRaises(ValidationError, partition.add_filesystem,
                          **{'fstype': FILESYSTEM_TYPE.EXT4})

    def test_partition_remove_filesystem(self):
        """Tests filesystem removal from a partition"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        partition.add_filesystem(fstype=FILESYSTEM_TYPE.EXT4)
        # After removal, partition.filesystem should be None
        partition.remove_filesystem()
        self.assertIsNone(partition.filesystem)

    def test_partition_remove_absent_filesystem(self):
        """Tests whether attempting to remove a non-existent FS fails"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        # Removal should do nothing
        self.assertIsNone(partition.remove_filesystem())

    def test_filesystem_returns_filesystem(self):
        """Checks that the get_filesystem method returns the filesystem that's
        on the partition"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        fs = factory.make_Filesystem(partition=partition)
        self.assertEqual(partition.filesystem.id, fs.id)

    def test_filesystem_returns_none(self):
        """Checks that the get_filesystem method returns none when there is no
        filesystem on the partition"""
        partition_table = factory.make_PartitionTable()
        partition = partition_table.add_partition()
        self.assertIsNone(partition.filesystem)

    def test_get_partition_number_returns_starting_at_1_in_order_for_gpt(self):
        node = factory.make_Node(bios_boot_method="uefi")
        block_device = factory.make_PhysicalBlockDevice(
            node=node,
            size=(MIN_BLOCK_DEVICE_SIZE * 4) + PARTITION_TABLE_EXTRA_SPACE)
        partition_table = factory.make_PartitionTable(
            block_device=block_device, table_type=PARTITION_TABLE_TYPE.GPT)
        partitions = [
            partition_table.add_partition(size=MIN_BLOCK_DEVICE_SIZE)
            for _ in range(4)
        ]
        idx = 1
        for partition in partitions:
            self.expectThat(idx, Equals(partition.get_partition_number()))
            idx += 1

    def test_get_partition_number_returns_correct_numbering_for_mbr(self):
        block_device = factory.make_PhysicalBlockDevice(
            size=(MIN_BLOCK_DEVICE_SIZE * 6) + PARTITION_TABLE_EXTRA_SPACE)
        partition_table = factory.make_PartitionTable(
            block_device=block_device, table_type=PARTITION_TABLE_TYPE.MBR)
        partitions = [
            partition_table.add_partition(size=MIN_BLOCK_DEVICE_SIZE)
            for _ in range(6)
        ]
        idx = 1
        for partition in partitions:
            self.expectThat(idx, Equals(partition.get_partition_number()))
            idx += 1
            if idx == 4:
                # Skip the extended partition.
                idx += 1

    def test_delete_not_allowed_if_part_of_filesystem_group(self):
        partition = factory.make_Partition()
        VolumeGroup.objects.create_volume_group(
            factory.make_name("vg"), [], [partition])
        error = self.assertRaises(ValidationError, partition.delete)
        self.assertEquals(
            "Cannot delete partition because its part of a volume group.",
            error.message)

    def test_delete(self):
        partition = factory.make_Partition()
        partition.delete()
        self.assertIsNone(reload_object(partition))
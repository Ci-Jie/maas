# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `BootSourceSelection`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from maasserver.models import (
    BootSource,
    BootSourceSelection,
    )
from maasserver.testing.factory import factory
from maastesting.testcase import MAASTestCase


class TestBootSourceSelection(MAASTestCase):
    """Tests for the `BootSourceSelection` model."""

    def test_can_create_selection(self):
        cluster = factory.make_node_group()
        boot_source = BootSource(
            cluster=cluster,
            url="http://example.com",
            keyring_filename="/path/to/something")
        boot_source.save()
        selection = BootSourceSelection(
            boot_source=boot_source,
            release="trusty", arches=["i386"], subarches=["generic"],
            labels=["release"])
        selection.save()
        self.assertEqual(
            (
                "trusty",
                ["i386"],
                ["generic"],
                ["release"]
            ),
            (
                selection.release,
                selection.arches,
                selection.subarches,
                selection.labels,
            ))

    def test_deleting_boot_source_deletes_its_selections(self):
        # BootSource deletion cascade-deletes related
        # BootSourceSelections. This is implicit in Django but it's
        # worth adding a test for it all the same.
        boot_source = factory.make_boot_source()
        boot_source_selection = factory.make_boot_source_selection(
            boot_source=boot_source)
        boot_source.delete()
        self.assertNotIn(
            boot_source_selection.id,
            [selection.id for selection in BootSourceSelection.objects.all()])

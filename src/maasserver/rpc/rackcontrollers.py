# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""RPC helpers relating to rack controllers."""

__all__ = [
    "register_rackcontroller",
]


from django.db import (
    IntegrityError,
    transaction,
)
from django.db.models import Q
from maasserver import worker_user
from maasserver.enum import NODE_TYPE
from maasserver.models import (
    Interface,
    Node,
    NodeGroupToRackController,
    RackController,
)
from maasserver.models.node import typecast_node
from maasserver.utils.orm import transactional
from provisioningserver.logger import get_maas_logger
from provisioningserver.utils.twisted import synchronous


maaslog = get_maas_logger('rpc.rackcontrollers')


@synchronous
@transactional
def handle_upgrade(rack_controller, nodegroup_uuid):
    """Handle upgrading from MAAS 1.9. Set the VLAN the rack controller
    should manage."""
    if (nodegroup_uuid is not None and
            len(nodegroup_uuid) > 0 and
            not nodegroup_uuid.isspace()):
        ng_to_racks = NodeGroupToRackController.objects.filter(
            uuid=nodegroup_uuid)
        vlans = [
            ng_to_rack.subnet.vlan
            for ng_to_rack in ng_to_racks
        ]
        # The VLAN object can only be related to a RackController
        for nic in rack_controller.interface_set.all():
            if nic.vlan in vlans:
                nic.vlan.primary_rack = rack_controller
                nic.vlan.dhcp_on = True
                nic.vlan.save()
                maaslog.info(
                    "DHCP setting from NodeGroup(%s) have been migrated "
                    "to %s." % (nodegroup_uuid, nic.vlan))
        for ng_to_rack in ng_to_racks:
            ng_to_rack.delete()


@synchronous
@transactional
def register_rackcontroller(
        system_id=None, hostname='', interfaces={}, url=None,
        nodegroup_uuid=None):
    """Register a new rack controller if not already registered.

    Attempt to see if the rack controller was already registered as a node.
    This can be looked up either by system_id, hostname, or mac address. If
    found convert the existing node into a rack controller. If not found create
    a new rack controller. After the rack controller has been registered and
    successfully connected we will refresh all commissioning data."""
    rackcontroller = find_and_register_existing(
        system_id, hostname, interfaces)
    if rackcontroller is None:
        rackcontroller = register_new_rackcontroller(system_id, hostname)

    # Update `rackcontroller.url` from the given URL, but only when the
    # hostname is not 'localhost' (i.e. the default value used when the master
    # cluster connects).
    update_fields = []
    if url is not None and url.hostname != "localhost":
        if rackcontroller.url != url.geturl():
            rackcontroller.url = url.geturl()
            update_fields.append("url")
    work_user = worker_user.get_worker_user()
    if rackcontroller.owner != work_user:
        rackcontroller.owner = worker_user.get_worker_user()
        update_fields.append("owner")
    rackcontroller.save(update_fields=update_fields)
    return rackcontroller


def find_and_register_existing(system_id, hostname, interfaces):
    mac_addresses = set(
        interface["mac_address"]
        for _, interface in interfaces.items()
        if "mac_address" in interface
    )
    node = Node.objects.filter(
        Q(system_id=system_id) |
        Q(hostname=hostname) |
        Q(interface__mac_address__in=mac_addresses)).first()
    if node is None:
        return None
    # Refresh whenever an existing node is converted for use as a rack
    # controller. This is needed for two reasons. First, when the region starts
    # it creates a node for itself but only gathers networking information.
    # Second, information about the node may have changed since its last use.
    needs_refresh = True
    if node.node_type in (
            NODE_TYPE.RACK_CONTROLLER,
            NODE_TYPE.REGION_AND_RACK_CONTROLLER):
        maaslog.info(
            "Registering existing rack controller %s." % node.hostname)
        # We don't want to refresh existing rack controllers as each time a
        # rack controller connects to a region it creates four connections.
        # This means for every region we connect to we would refresh
        # 4 * regions every time the rack controller restarts. Our information
        # at this point should be current anyway and the user can always
        # manually refresh.
        needs_refresh = False
    elif node.node_type == NODE_TYPE.REGION_CONTROLLER:
        maaslog.info(
            "Converting %s into a region and rack controller." % node.hostname)
        node.node_type = NODE_TYPE.REGION_AND_RACK_CONTROLLER
        node.save()
    else:
        maaslog.info("Converting %s into a rack controller." % node.hostname)
        node.node_type = NODE_TYPE.RACK_CONTROLLER
        node.save()

    rackcontroller = typecast_node(node, RackController)
    # Tell register RPC call a refresh isn't needed
    rackcontroller.needs_refresh = needs_refresh
    return rackcontroller


def register_new_rackcontroller(system_id, hostname):
    try:
        with transaction.atomic():
            rackcontroller = RackController.objects.create(hostname=hostname)
            # Tell register RPC call a refresh is needed
            rackcontroller.needs_refresh = True
    except IntegrityError as e:
        # regiond runs on each server with four threads. When a new rack
        # controller connects it connects to all threads on all servers.  We
        # use the fact that hostnames must be unique to prevent us from
        # creating multiple node objects for a single node.
        maaslog.info(
            "Rack controller(%s) currently being registered, retrying..." %
            hostname)
        rackcontroller = find_and_register_existing(system_id, hostname, {})
        if rackcontroller is not None:
            return rackcontroller
        else:
            # If we still can't find it something has gone wrong so throw the
            # exception
            raise e from None
    maaslog.info(
        "%s has been created as a new rack controller" %
        rackcontroller.hostname)
    return rackcontroller


@transactional
def update_foreign_dhcp_ip(cluster_uuid, interface_name, foreign_dhcp_ip):
    """Update the foreign_dhcp_ip field of a given interface on a cluster.

    Note: We do this through an update, not a read/modify/write.
    Updating NodeGroupInterface client-side may inadvertently trigger
    Django signals that cause a rewrite of the DHCP config, plus restart
    of the DHCP server.  The inadvertent triggering has been known to
    happen because of race conditions between read/modify/write
    transactions that were enabled by Django defaulting to, and being
    designed for, the READ COMMITTED isolation level; the ORM writing
    back even unmodified fields; and GenericIPAddressField's default
    value being prone to problems where NULL is sometimes represented as
    None, sometimes as an empty string, and the difference being enough
    to convince the signal machinery that these fields have changed when
    in fact they have not.

    :param cluster_uuid: Cluster's UUID.
    :param interface_name: The name of the cluster interface on which the
        foreign DHCP server was (or wasn't) discovered.
    :param foreign_dhcp_ip: IP address of foreign DCHP server, if any.
    """
    # XXX 2016-01-20 blake_r - Currently no where to place this information.
    # Need to add to the model to store this information.
    pass


@transactional
def get_rack_controllers_interfaces_as_dicts(system_id):
    """Return all the interfaces on a given rack controller as a list of dicts.

    :return: A list of dicts in the form {'name': interface.name,
        'interface': interface.interface, 'ip': interface.ip}, one dict per
        interface on the cluster.
    """
    interfaces = Interface.objects.filter(node__system_id=system_id)
    # XXX 2016-01-20 blake_r - Currently not passing any IP address as it now
    # should take a list of IP addresses and not just one IP address. To make
    # it work for now nothing its filtered out.
    return [
        {
            'name': interface.name,
            'interface': interface.name,
            'ip': '',
        }
        for interface in interfaces
        ]


@synchronous
@transactional
def update_interfaces(system_id, interfaces):
    """Update the interface definition on the rack controller."""
    rack_controller = RackController.objects.get(system_id=system_id)
    rack_controller.update_interfaces(interfaces)
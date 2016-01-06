# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Classes for generating BIND zone config files."""

__all__ = [
    'DNSForwardZoneConfig',
    'DNSReverseZoneConfig',
    'DomainInfo',
    ]

from datetime import datetime
from itertools import chain

from netaddr import (
    IPAddress,
    IPNetwork,
    spanning_cidr,
)
from netaddr.core import AddrFormatError
from provisioningserver.dns.config import (
    compose_config_path,
    render_dns_template,
    report_missing_config_dir,
)
from provisioningserver.utils.fs import incremental_write
from provisioningserver.utils.network import (
    intersect_iprange,
    ip_range_within_network,
)


def get_fqdn_or_ip_address(target):
    """Returns the ip address is target is a valid ip address, otherwise
    returns the target with appended '.' if missing."""
    try:
        return IPAddress(target).format()
    except AddrFormatError:
        return target.rstrip('.') + '.'


def enumerate_mapping(mapping):
    """Generate `(hostname, ip)` tuples from `mapping`.

    :param mapping: A dict mapping host names to lists of IP addresses.
    """
    for hostname, ips in mapping.items():
        for ip in ips:
            yield hostname, ip


def get_details_for_ip_range(ip_range):
    """For a given IPRange, return all subnets, a useable prefix and the
    reverse DNS suffix calculated from that IP range.

    :return: A tuple of:
        All subnets of /24 (or smaller if there is no /24 subnet to be
        found) in `ip_range`.
        A prefix made from the first two octets in the range.
        A RDNS suffix calculated from the first two octets in the range.
    """
    # Calculate a spanning network for the range above. There are
    # 256 /24 networks in a /16, so that's the most /24s we're going
    # to have to deal with; this matters later on when we iterate
    # through the /24s within this network.
    cidr = spanning_cidr(ip_range)
    subnets = cidr.subnet(max(24, cidr.prefixlen))

    # Split the spanning network into /24 subnets, then see if they fall
    # entirely within the original network range, partially, or not at
    # all.
    intersecting_subnets = []
    for subnet in subnets:
        intersect = intersect_iprange(subnet, ip_range)
        if intersect is None:
            # The subnet does not fall within the original network.
            pass
        else:
            # The subnet falls partially within the original network, so print
            # out a $GENERATE expression for a subset of the /24.
            intersecting_subnets.append(intersect)

    octet_one = (cidr.value & 0xff000000) >> 24
    octet_two = (cidr.value & 0x00ff0000) >> 16

    # The first two octets of the network range formatted in the
    # usual dotted-quad style. We can precalculate the start of any IP
    # address in the range because we're only ever dealing with /16
    # networks and smaller.
    prefix = "%d.%d" % (octet_one, octet_two)

    # Similarly, we can calculate what the reverse DNS suffix is going
    # to look like.
    rdns_suffix = "%d.%d.in-addr.arpa." % (octet_two, octet_one)
    return intersecting_subnets, prefix, rdns_suffix


class DomainInfo:
    """Information about a DNS zone"""

    def __init__(self, subnetwork, zone_name, target_path=None):
        """
        :param subnetwork: IPNetwork that this zone (chunk) is for.  None
            for forward zones.
        :param zone_name: Fully-qualified zone name
        :param target_path: Optional, can be used to override the target path.
        """
        self.subnetwork = subnetwork
        self.zone_name = zone_name
        if target_path is None:
            self.target_path = compose_config_path('zone.%s' % zone_name)
        else:
            self.target_path = target_path


class DomainConfigBase:
    """Base class for zone writers."""

    template_file_name = 'zone.template'

    def __init__(self, domain, zone_info, serial=None):
        """
        :param domain: An iterable list of domain names for the
            forward zone.
        :param zone_info: list of DomainInfo entries.
        :param serial: The serial to use in the zone file. This must increment
            on each change.
        """
        self.domain = domain
        self.serial = serial
        self.zone_info = zone_info
        self.target_base = compose_config_path('zone')

    def make_parameters(self):
        """Return a dict of the common template parameters."""
        return {
            'domain': self.domain,
            'serial': self.serial,
            'modified': str(datetime.today()),
        }

    @classmethod
    def write_zone_file(cls, output_file, *parameters):
        """Write a zone file based on the zone file template.

        There is a subtlety with zone files: their filesystem timestamp must
        increase with every rewrite.  Some filesystems (ext3?) only seem to
        support a resolution of one second, and so this method may set an
        unexpected modification time in order to maintain that property.
        """
        if not isinstance(output_file, list):
            output_file = [output_file]
        for outfile in output_file:
            content = render_dns_template(cls.template_file_name, *parameters)
            with report_missing_config_dir():
                incremental_write(content.encode("utf-8"), outfile, mode=0o644)
        pass


class DNSForwardZoneConfig(DomainConfigBase):
    """Writes forward zone files.

    A forward zone config contains two kinds of mappings: "A" records map all
    possible IP addresses within each of its networks to generated hostnames
    based on those addresses.  "CNAME" records map configured hostnames to the
    matching generated IP hostnames.  An additional "A" record maps the domain
    to the name server itself.
    """

    def __init__(self, domain, **kwargs):
        """See `DomainConfigBase.__init__`.

        :param domain: The forward domain name.
        :param serial: The serial to use in the zone file. This must increment
            on each change.
        :param dns_ip: The IP address of the DNS server authoritative for this
            zone.
        :param mapping: A hostname:ip-addresses mapping for all known hosts in
            the zone.  They will be mapped as A records.
        :param srv_mapping: Set of SRVRecord mappings.
        """
        self._dns_ip = kwargs.pop('dns_ip', None)
        self._mapping = kwargs.pop('mapping', {})
        self._network = kwargs.pop('network', None)
        self._dynamic_ranges = kwargs.pop('dynamic_ranges', [])
        self._srv_mapping = kwargs.pop('srv_mapping', [])
        super(DNSForwardZoneConfig, self).__init__(
            domain,
            zone_info=[DomainInfo(None, domain)],
            **kwargs)

    @classmethod
    def get_mapping(cls, mapping, domain, dns_ip):
        """Return a generator mapping hostnames to IP addresses.

        This includes the record for the name server's IP.
        :param mapping: A dict mapping host names to lists of IP addresses.
        :param domain: Zone's domain name.
        :param dns_ip: IP address for the zone's authoritative DNS server.
        :return: A generator of tuples: (host name, IP address).
        """
        return chain(
            [('%s.' % domain, dns_ip)],
            enumerate_mapping(mapping))

    @classmethod
    def get_A_mapping(cls, mapping, domain, dns_ip):
        """Return a generator mapping hostnames to IP addresses for all
        the IPv4 addresses in `mapping`.

        The returned mapping is meant to be used to generate A records in
        the forward zone file.

        This includes the A record for the name server's IP.
        :param mapping: A dict mapping host names to lists of IP addresses.
        :param domain: Zone's domain name.
        :param dns_ip: IP address for the zone's authoritative DNS server.
        :return: A generator of tuples: (host name, IP address).
        """
        mapping = cls.get_mapping(mapping, domain, dns_ip)
        if mapping is None:
            return ()
        return (item for item in mapping if IPAddress(item[1]).version == 4)

    @classmethod
    def get_AAAA_mapping(cls, mapping, domain, dns_ip):
        """Return a generator mapping hostnames to IP addresses for all
        the IPv6 addresses in `mapping`.

        The returned mapping is meant to be used to generate AAAA records
        in the forward zone file.

        :param mapping: A dict mapping host names to lists of IP addresses.
        :param domain: Zone's domain name.
        :param dns_ip: IP address for the zone's authoritative DNS server.
        :return: A generator of tuples: (host name, IP address).
        """
        mapping = cls.get_mapping(mapping, domain, dns_ip)
        if mapping is None:
            return ()
        return (item for item in mapping if IPAddress(item[1]).version == 6)

    @classmethod
    def get_srv_mapping(cls, mappings):
        """Return a generator mapping srv entries to hostnames.

        :param mappings: Set of SRVRecord.
        :return: A generator of tuples:
            (service, 'priority weight port target').
        """
        if mappings is None:
            return
        for record in mappings:
            target = get_fqdn_or_ip_address(record.target)
            item = '%s %s %s %s' % (
                record.priority,
                record.weight,
                record.port,
                target)
            yield (record.service, item)

    @classmethod
    def get_GENERATE_directives(cls, dynamic_range):
        """Return the GENERATE directives for the forward zone of a network.
        """
        slash_16 = IPNetwork("%s/16" % IPAddress(dynamic_range.first))
        if (dynamic_range.size > 256 ** 2 or
           not ip_range_within_network(dynamic_range, slash_16)):
            # We can't issue a sane set of $GENERATEs for any network
            # larger than a /16, or for one that spans two /16s, so we
            # don't try.
            return []

        generate_directives = set()
        subnets, prefix, rdns_suffix = get_details_for_ip_range(dynamic_range)
        for subnet in subnets:
            iterator = "%d-%d" % (
                (subnet.first & 0x000000ff),
                (subnet.last & 0x000000ff))

            hostname = "%s-%d-$" % (
                prefix.replace('.', '-'),
                # Calculate what the third quad (i.e. 10.0.X.1) value should
                # be for this subnet.
                (subnet.first & 0x0000ff00) >> 8,
                )

            ip_address = "%s.%d.$" % (
                prefix,
                (subnet.first & 0x0000ff00) >> 8)
            generate_directives.add((iterator, hostname, ip_address))

        return sorted(
            generate_directives, key=lambda directive: directive[2])

    def write_config(self):
        """Write the zone file."""
        # Create GENERATE directives for IPv4 ranges.
        for zi in self.zone_info:
            generate_directives = list(
                chain.from_iterable(
                    self.get_GENERATE_directives(dynamic_range)
                    for dynamic_range in self._dynamic_ranges
                    if dynamic_range.version == 4
                ))
            self.write_zone_file(
                zi.target_path, self.make_parameters(),
                {
                    'mappings': {
                        'SRV': self.get_srv_mapping(
                            self._srv_mapping),
                        'A': self.get_A_mapping(
                            self._mapping, self.domain, self._dns_ip),
                        'AAAA': self.get_AAAA_mapping(
                            self._mapping, self.domain, self._dns_ip),
                    },
                    'generate_directives': {
                        'A': generate_directives,
                    }
                })


class DNSReverseZoneConfig(DomainConfigBase):
    """Writes reverse zone files.

    A reverse zone mapping contains "PTR" records, each mapping
    reverse-notation IP addresses within a network to the matching generated
    hostname.
    """

    def __init__(self, domain, **kwargs):
        """See `DomainConfigBase.__init__`.

        :param domain: Default zone name.
        :param serial: The serial to use in the zone file. This must increment
            on each change.
        :param mapping: A hostname:ips mapping for all known hosts in
            the reverse zone.  They will be mapped as PTR records.  IP
            addresses not in `network` will be dropped.
        :param network: The network that the mapping exists within.
        :type network: :class:`netaddr.IPNetwork`
        """
        self._mapping = kwargs.pop('mapping', {})
        self._network = kwargs.pop("network", None)
        self._dynamic_ranges = kwargs.pop('dynamic_ranges', [])
        zone_info = self.compose_zone_info(self._network)
        super(DNSReverseZoneConfig, self).__init__(
            domain, zone_info=zone_info, **kwargs)

    @classmethod
    def compose_zone_info(cls, network):
        """Return the names of the reverse zones."""
        # Generate the name of the reverse zone file:
        # Use netaddr's reverse_dns() to get the reverse IP name
        # of the first IP address in the network and then drop the first
        # octets of that name (i.e. drop the octets that will be specified in
        # the zone file).
        # returns a list of (IPNetwork, zone_name, zonefile_path) tuples
        info = []
        first = IPAddress(network.first)
        last = IPAddress(network.last)
        if first.version == 6:
            # IPv6.
            # 2001:89ab::/19 yields 8.1.0.0.2.ip6.arpa, and the full list
            # is 8.1.0.0.2.ip6.arpa, 9.1.0.0.2.ip6.arpa
            # The ipv6 reverse dns form is 32 elements of 1 hex digit each.
            # How many elements of the reverse DNS name to we throw away?
            # Prefixlen of 0-3 gives us 1, 4-7 gives us 2, etc.
            # While this seems wrong, we always _add_ a base label back in,
            # so it's correct.
            rest_limit = (132 - network.prefixlen) // 4
            # What is the prefix for each inner subnet (It will be the next
            # smaller multiple of 4.)  If it's the smallest one, then RFC2317
            # tells us that we're adding an extra blob to the front of the
            # reverse zone name, and we want the entire prefixlen.
            subnet_prefix = (network.prefixlen + 3) // 4 * 4
            if subnet_prefix == 128:
                subnet_prefix = network.prefixlen
            # How big is the step between subnets?  Again, special case for
            # extra small subnets.
            step = 1 << ((128 - network.prefixlen) // 4 * 4)
            if step < 16:
                step = 16
            # Grab the base (hex) and trailing labels for our reverse zone.
            split_zone = first.reverse_dns.split('.')
            zone_rest = ".".join(split_zone[rest_limit:-1])
            base = int(split_zone[rest_limit - 1], 16)
        else:
            # IPv4.
            # The logic here is the same as for IPv6, but with 8 instead of 4.
            rest_limit = (40 - network.prefixlen) // 8
            subnet_prefix = (network.prefixlen + 7) // 8 * 8
            if subnet_prefix == 32:
                subnet_prefix = network.prefixlen
            step = 1 << ((32 - network.prefixlen) // 8 * 8)
            if step < 256:
                step = 256
            # Grab the base (decimal) and trailing labels for our reverse
            # zone.
            split_zone = first.reverse_dns.split('.')
            zone_rest = ".".join(split_zone[rest_limit:-1])
            base = int(split_zone[rest_limit - 1])
        while first <= last:
            # Rest_limit has bounds of 1..labelcount+1 (5 or 33).
            # If we're stripping any elements, then we just want base.name.
            if rest_limit > 1:
                if first.version == 6:
                    new_zone = "%x.%s" % (base, zone_rest)
                else:
                    new_zone = "%d.%s" % (base, zone_rest)
            # We didn't actually strip any elemnts, so base goes back with
            # the prefixlen attached.
            elif first.version == 6:
                new_zone = "%x-%d.%s" % (base, network.prefixlen, zone_rest)
            else:
                new_zone = "%d-%d.%s" % (base, network.prefixlen, zone_rest)
            info.append(DomainInfo(
                IPNetwork("%s/%d" % (first, subnet_prefix)),
                new_zone))
            base += 1
            try:
                first += step
            except IndexError:
                # IndexError occurs when we go from 255.255.255.255 to
                # 0.0.0.0.  If we hit that, we're all fine and done.
                break
        return info

    @classmethod
    def get_PTR_mapping(cls, mapping, domain, network):
        """Return reverse mapping: reverse IPs to hostnames.

        The reverse generated mapping is the mapping between the reverse
        IP addresses and all the hostnames for all the IP addresses in the
        given `mapping`.

        The returned mapping is meant to be used to generate PTR records in
        the reverse zone file.

        :param mapping: A hostname:ip-addresses mapping for all known hosts in
            the reverse zone, to their FQDN (without trailing dot).
        :param domain: Zone's domain name.
        :param network: DNS Zone's network.
        :type network: :class:`netaddr.IPNetwork`
        """
        if mapping is None:
            return ()
        return (
            (
                IPAddress(ip).reverse_dns,
                '%s.' % (hostname),
            )
            for hostname, ip in enumerate_mapping(mapping)
            # Filter out the IP addresses that are not in `network`.
            if IPAddress(ip) in network
        )

    @classmethod
    def get_GENERATE_directives(cls, dynamic_range, domain, zone_info):
        """Return the GENERATE directives for the reverse zone of a network."""
        slash_16 = IPNetwork("%s/16" % IPAddress(dynamic_range.first))
        if (dynamic_range.size > 256 ** 2 or
           not ip_range_within_network(dynamic_range, slash_16)):
            # We can't issue a sane set of $GENERATEs for any network
            # larger than a /16, or for one that spans two /16s, so we
            # don't try.
            return []

        generate_directives = set()
        # The largest subnet returned is a /24.
        subnets, prefix, rdns_suffix = get_details_for_ip_range(dynamic_range)
        for subnet in subnets:
            if (IPAddress(subnet.first) in zone_info.subnetwork):
                iterator = "%d-%d" % (
                    (subnet.first & 0x000000ff),
                    (subnet.last & 0x000000ff))
                hostname = "%s-%d-$" % (
                    prefix.replace('.', '-'),
                    (subnet.first & 0x0000ff00) >> 8)
                # If we're at least a /24, then fully specify the name,
                # rather than trying to figure out how much of the name
                # is in the zone name.
                if zone_info.subnetwork.prefixlen <= 24:
                    rdns = "$.%d.%s" % (
                        (subnet.first & 0x0000ff00) >> 8,
                        rdns_suffix)
                else:
                    # Let the zone declaration provide the suffix.
                    # rather than trying to calculate it.
                    rdns = "$"
                generate_directives.add(
                    (iterator, rdns, "%s.%s." % (hostname, domain)))

        return sorted(
            generate_directives, key=lambda directive: directive[2])

    def write_config(self):
        """Write the zone file."""
        # Create GENERATE directives for IPv4 ranges.
        for zi in self.zone_info:
            generate_directives = list(
                chain.from_iterable(
                    self.get_GENERATE_directives(
                        dynamic_range,
                        self.domain,
                        zi)
                    for dynamic_range in self._dynamic_ranges
                    if dynamic_range.version == 4
                ))
            self.write_zone_file(
                zi.target_path, self.make_parameters(),
                {
                    'mappings': {
                        'PTR': self.get_PTR_mapping(
                            self._mapping, self.domain, zi.subnetwork),
                    },
                    'generate_directives': {
                        'PTR': generate_directives,
                    }
                }
            )
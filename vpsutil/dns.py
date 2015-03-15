"""
Module for dealing with creation and modification of DNS records
"""

from vpsutil.core import LinodeAPI


class DNSManagerCore(object):
    def create_record(self, domain, record_type, name, target, ttl=None):
        raise NotImplementedError

    def update_record(self, domain, record_type, name, target):
        raise NotImplementedError

    def delete_record(self, domain, record_type, name):
        raise NotImplementedError


class LinodeDNSManager(LinodeAPI, DNSManagerCore):
    def create_record(self, domain, record_type, name, target, ttl=0):
        domain_id = domain
        if not isinstance(domain, int):
            domain_id = self.get_domain_id(domain)

        return self.post(
            "domain.resource.create",
            DomainID=domain_id, Type=record_type, Name=name, Target=target)

    def update_record(self, domain, record_type, name, target):
        domain_id = self.get_domain_id(domain)

        try:
            record_id = self.get_domain_resource_id(
                domain_id, record_type, name)
        except ValueError:
            self.create_record(domain, record_type, name, target)
            return

        return self.post(
            "domain.resource.update",
            DomainID=domain_id,
            ResourceID=record_id, Type=record_type, Name=name, Target=target)

    def delete_record(self, domain, record_type, name):
        domain_id = self.get_domain_id(domain)
        resource_id = self.get_domain_resource_id(domain_id, record_type, name)

        return self.post(
            "domain.resource.delete",
            DomainID=domain_id, ResourceID=resource_id)


# TODO: Digital Ocean implementation?  Maybe not, their DNS options seem
# somewhat more limited

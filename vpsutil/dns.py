"""
Module for dealing with creation and modification of DNS records
"""


class DNSManagerCore(object):
    def create_domain(self, name):
        raise NotImplementedError

    def create_record(self, domain, record_type, target, ttl=None):
        raise NotImplementedError

    def update_record(self, domain, record_type, target):
        pass


class LinodeDNSManager(DNSManagerCore):
    pass
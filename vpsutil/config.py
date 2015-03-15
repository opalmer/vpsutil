from configparser import ConfigParser
from collections import namedtuple
from os.path import join, expanduser


_Providers = namedtuple("Providers", ("LINODE", "DIGITAL_OCEAN"))
Providers = _Providers(LINODE="linode", DIGITAL_OCEAN="digital_ocean")

config = ConfigParser()
config.read([join(expanduser("~"), ".vpsutil")])

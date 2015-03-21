from configparser import ConfigParser
from collections import namedtuple
from os.path import join, expanduser

CONFIG_DIR = join(expanduser("~"), ".vpsutil")
CONFIG_FILE = join(CONFIG_DIR, "config")
CONFIG_DIR_SSH = join(CONFIG_DIR, "ssh")

# In case we support other providers in the future
_Providers = namedtuple("Providers", ("DEFAULT", "DIGITAL_OCEAN", ))
Providers = _Providers(DEFAULT="digital_ocean", DIGITAL_OCEAN="digital_ocean")

config = ConfigParser()
config.read(CONFIG_FILE)

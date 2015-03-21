import random
import subprocess
import time
from collections import namedtuple
from configparser import NoOptionError
from os.path import expanduser, isfile

try:
    from http.client import NOT_FOUND, UNPROCESSABLE_ENTITY
except ImportError:
    from httplib import NOT_FOUND, UNPROCESSABLE_ENTITY

from requests import Session as _Session
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError

from vpsutil.config import Providers, config
from vpsutil.logger import logger


_PowerState = namedtuple("PowerStates", ("ON", "OFF", "RESET"))
PowerState = _PowerState(ON="on", OFF="off", RESET="reset")


class Base(_Session):
    URL = "https://api.digitalocean.com/v2"

    def __init__(self):
        super(Base, self).__init__()
        self.mount("https://", HTTPAdapter(max_retries=5))
        self.headers.update(
            Authorization="Bearer %s" % config.get(
                Providers.DIGITAL_OCEAN, "token"))


class Domains(Base):
    def get_domain(self, domain):
        pass

    def get_record(self, domain, record_type, name):
        assert isinstance(domain, str)
        assert isinstance(record_type, str) and record_type.isupper()
        assert isinstance(name, str)
        query = {"domain": domain, "type": record_type, "name": name}
        logger.info("Searching for domain record %r", query)

        response = self.get(
            self.URL + "/domains/%s/records" % domain
        )
        response.raise_for_status()
        data = response.json()
        records = []
        for record in data["domain_records"]:
            if record["type"] != record_type or record["name"] != name:
                continue
            records.append(record)

        if len(records) > 1:
            raise ValueError(
                "Found more than one domain record matching %r" % query)
        elif not records:
            return None
        else:
            return records[0]

    def update_record(
            self, domain, record_type, name, target,
            priority=None, port=None, weight=None, must_exist=False):
        """
        Creates a new records or updates an existing entry.  In the case
        of updating an existing entry we update the existing record
        """
        assert isinstance(domain, str)
        assert isinstance(record_type, str) and record_type.isupper()
        assert isinstance(name, str)
        assert isinstance(target, str)
        query = {
            "domain": domain, "type": record_type,
            "name": name, "target": target}
        current_record = self.get_record(domain, record_type, name)
        record_data = {
            "type": record_type,
            "name": name,
            "data": target,
        }

        if record_type in ("MX", "SRV"):
            record_data.update(priority=priority)

        if record_type == "SRV":
            record_data.update(
                port=port, weight=weight
            )

        if current_record is None and must_exist:
            raise ValueError(
                "Domain record for %r does not exist." % query)

        # Create new record
        elif current_record is None and not must_exist:
            logger.info("Creating domain record %r", query)
            response = self.post(
                self.URL + "/domains/%s/records" % domain,
                data=record_data
            )

        # Update existing record
        else:
            logger.info("Updating domain record %r", query)
            response = self.put(
                self.URL + "/domains/%s/records/%d" % (
                    domain, current_record["id"]),
                data=record_data
            )

        response.raise_for_status()
        return response.json()["domain_record"]

    def delete_record(self, domain, record_type, name):
        assert record_type.is_upper()
        query = {"domain": domain, "type": record_type, "name": name}
        logger.info("Delete domain record %r", query)

        record = self.get_record(domain, record_type, name)
        if record is None:
            return

        response = self.delete(
            self.URL + "/domains/%s/records/%d" % (domain, record["id"])
        )
        response.raise_for_status()


class Search(Base):
    def distributions(self, slug=None, regions=None):
        if slug is None:
            slug = config.get(Providers.DIGITAL_OCEAN, "default_image")

        if regions is None:
            regions = []

        logger.info(
            "Searching for distribution %s (regions: %s)", slug, regions)
        response = self.get(self.URL + "/images", params={"distribution": True})
        response.raise_for_status()
        data = response.json()

        for dist in data["images"]:
            if not dist["public"]:
                logger.debug("... %s not public", dist["slug"])
                continue

            # At least one of the region slugs which came from
            # regions() must be present in the distribution
            for region_slug in regions:
                if region_slug in dist["regions"]:
                    break
                else:
                    logger.debug(
                        "... %s region not in %s", region_slug, dist["slug"])
            else:
                continue

            if dist["slug"] == slug:
                return dist
        else:
            raise ValueError("Failed to locate distribution")

    def regions(self, size, slug_prefix=None, features=None):
        """
        Returns a list of regions for a given search criteria

        :param string size:
            The size of the instance you are looking for.  (ex. 512mb, 1gb)

        :param string slug_prefix:
            The prefix of of the region we're searching in (ex. 'ny')

        :param list features:
            An option list of features that's needed in the region
            (ex. ['metadata', 'ipv6'])
        """
        assert isinstance(size, str)
        assert slug_prefix is None or isinstance(slug_prefix, str)
        assert features is None or isinstance(features, (list, tuple))
        logger.info(
            "Searching for region(s) matching %r",
            {"size": size,
             "slug_prefix": slug_prefix or "any",
             "features": features or "any"})

        if features is None:
            features = []

        if slug_prefix is None:
            try:
                slug_prefix = config.get(
                    Providers.DIGITAL_OCEAN, "region_slug_prefix")
            except NoOptionError:
                pass

        response = self.get(self.URL + "/regions")
        response.raise_for_status()
        data = response.json()

        regions = []
        for region in data["regions"]:
            if not region["available"]:
                logger.debug("... %s - not available", region["slug"])
                continue

            if slug_prefix is not None \
                    and not region["slug"].startswith(slug_prefix):
                logger.debug("... %s - wrong slug prefix", region["slug"])
                continue

            if size not in region["sizes"]:
                logger.debug(
                    "... %s - does not support this size", region["slug"])
                continue

            missing_feature = False
            for feature in features:
                if feature not in region["features"]:
                    missing_feature = True
                    logger.debug(
                        "... %s - missing feature %s", region["slug"], feature)
                    break

            if missing_feature:
                logger.debug(
                    "... %s - missing one or more features", region["slug"])
                continue

            regions.append(region)

        logger.debug("... regions: %r", [region["slug"] for region in regions])
        return regions


class SSH(Base):
    def public_keys(self):
        logger.info("Retrieving public SSH keys")
        response = self.get(self.URL + "/account/keys")
        response.raise_for_status()
        return response.json()["ssh_keys"]

    def _get_ssh_fingerprint(self, path):
        bits, fingerprint, comment, typename = subprocess.check_output(
            ["ssh-keygen", "-lf", path]).strip().split()
        return fingerprint.decode("utf-8"), comment.decode("utf-8")

    def upload_public_ssh_key(self, name=None, path=None):
        if path is None:
            path = expanduser(config.get(Providers.DIGITAL_OCEAN, "public_key"))

        # Retrieve the signature of this key, we'll use this to determine
        # if the key has already been uploaded.
        assert isfile(path)
        fingerprint, comment = self._get_ssh_fingerprint(path)

        logger.debug(
            "Checking to see if public key %s has been uploaded", fingerprint)
        for public_key in self.public_keys():
            if public_key["fingerprint"] == fingerprint:
                logger.debug("... key exists")
                break
        else:
            logger.info("Uploading public key %s", path)
            response = self.post(
                self.URL + "/account/keys",
                data={
                    "name": name or comment,
                    "public_key": open(path, "r").read()
                }
            )
            response.raise_for_status()


class Droplets(Base):
    def __init__(self, search, ssh):
        super(Droplets, self).__init__()
        assert isinstance(search, Search)
        assert isinstance(ssh, SSH)
        self.search = search
        self.ssh = ssh

    def create(
            self, hostname, size, distribution=None, bootstrap=None):
        features = []
        if bootstrap:
            features.append("metadata")

        regions = self.search.regions(size, features=features)
        region_slugs = [region["slug"] for region in regions]
        distribution = self.search.distributions(
            slug=distribution, regions=region_slugs)

        # Get all regions which both regions() and distributions()
        # agree on.
        region_slug = random.choice(
            list(set(distribution["regions"]) & set(region_slugs)))

        logger.info("Creating %s @ %s in %s", hostname, size, region_slug)
        data = {
            "name": hostname,
            "region": region_slug,
            "size": size,
            "image": distribution["id"]
        }

        public_key_ids = [
            public_key["id"] for public_key in self.ssh.public_keys()]

        if public_key_ids:
            data.update(public_keys=public_key_ids)

        if bootstrap and isfile(bootstrap):
            bootstrap = open(bootstrap, "r").read()

        if bootstrap:
            data.update(user_data=bootstrap)

        # Create the host
        response = self.post(
            self.URL + "/droplets",
            data=data
        )
        response.raise_for_status()
        return response.json()["droplet"]

    def power(self, droplet_id, state):
        logger.info("Set power state of droplet %d to %s", droplet_id, state)
        if state is PowerState.ON:
            response = self.post(
                self.URL + "/droplets/%d/actions" % droplet_id,
                data={"type": "power_on"}
            )
            response.raise_for_status()
            return response.json()["action"]

        raise NotImplementedError(state)

    def destroy(self, droplet):
        if isinstance(droplet, int):
            try:
                droplet = self.get_droplet(droplet)
            except HTTPError as e:
                if e.response.status_code == NOT_FOUND:
                    logger.debug("... %d does not exist", droplet)
                    return
                raise

        assert isinstance(droplet, dict)
        logger.info("Destroy droplet %d", droplet["id"])

        while True:
            response = self.delete(self.URL + "/droplets/%d" % droplet["id"])
            if response.status_code == UNPROCESSABLE_ENTITY:
                time.sleep(5)
                logger.debug("... retry")
                continue

            response.raise_for_status()
            break

    def get_droplet(self, droplet_id):
        assert isinstance(droplet_id, int)
        logger.info("Get droplet %d", droplet_id)
        response = self.get(self.URL + "/droplets/%d" % droplet_id)
        response.raise_for_status()
        return response.json()["droplet"]

    def get_public_ip(self, droplet, ip_version="v4"):
        if isinstance(droplet, int):
            # We might not have a network yet
            while True:
                result = self.get_droplet(droplet)
                if result["networks"][ip_version]:
                    droplet = result
                    break
                time.sleep(3)

        logger.info("Get IP of droplet %s", droplet["id"])
        addresses = []
        for address in droplet["networks"][ip_version]:
            if address["type"] == "public":
                addresses.append(address["ip_address"])

        assert len(addresses) == 1
        return addresses[0]

    def find_droplets(self, **fields):
        response = self.get(self.URL + "/droplets")
        response.raise_for_status()
        data = response.json()

        for droplet in data["droplets"]:
            for key, value in fields.items():
                if key not in ("ip", ):
                    if key not in droplet:
                        raise KeyError("No such key %s" % key)

                    if droplet[key] != value:
                        break

                if key == "ip" and value != self.get_public_ip(droplet):
                    break

            else:
                yield droplet

    def find_droplet(self, **fields):
        droplets = list(self.find_droplets(**fields))
        if not droplets:
            raise ValueError("No droplets found matching %s" % fields)
        if len(droplets) > 1:
            raise ValueError("Found multiple droplets matching %s" % fields)
        return droplets[0]


class DigitalOcean(object):
    """
    High level wrapper around the various sub APIs.
    """
    def __init__(self):
        self.ssh = SSH()
        self.search = Search()
        self.droplets = Droplets(self.search, self.ssh)
        self.dns = Domains()



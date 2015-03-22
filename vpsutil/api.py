import random
import subprocess
import time
import json
from collections import namedtuple
from configparser import NoOptionError
from os.path import expanduser, isfile
from pprint import pformat

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
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer %s" % config.get(
                Providers.DIGITAL_OCEAN, "token")
        })

    def request(self, *args, **kwargs):
        # It's magic...but it saves time.  Wouldn't really expect
        # the requests package to do this for us anyway.
        if (self.headers.get("Content-Type") == "application/json"
                and "data" in kwargs):
            kwargs["data"] = json.dumps(kwargs["data"])

        return super(Base, self).request(*args, **kwargs)


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
        assert record_type.isupper()
        query = {"domain": domain, "type": record_type, "name": name}

        record = self.get_record(domain, record_type, name)
        if record is None:
            return

        logger.warning("Delete domain record %r", query)
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
                # logger.debug("... %s - not available", region["slug"])
                continue

            if slug_prefix is not None \
                    and not region["slug"].startswith(slug_prefix):
                # logger.debug("... %s - wrong slug prefix", region["slug"])
                continue

            if size not in region["sizes"]:
                # logger.debug(
                #     "... %s - does not support this size", region["slug"])
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

    def _get_fingerprint(self, path):
        bits, fingerprint, comment, typename = subprocess.check_output(
            ["ssh-keygen", "-lf", path]).strip().split()
        return fingerprint.decode("utf-8"), comment.decode("utf-8")

    def upload_key(self, name=None, path=None):
        """
        Uploads a public ssh key to Digital Ocean.  If no path is provided
        we'll use the ``public_key`` entry from the config file.  Also if
        ``name`` is provided this will be used to set the name of the key
        instead of attempting to retrieve the name from the key file.
        """
        if path is None:
            path = expanduser(config.get(Providers.DIGITAL_OCEAN, "public_key"))

        with open(path, "r") as ssh_key:
            if "PRIVATE" in ssh_key.read():
                raise ValueError(
                    "%s appears to be a private key, we "
                    "expected a public key" % path)

        # Retrieve the signature of this key, we'll use this to determine
        # if the key has already been uploaded.
        assert isfile(path)
        if name is None:
            fingerprint, name = self._get_fingerprint(path)
        else:
            fingerprint, _ = self._get_fingerprint(path)

        if name is None or not name.strip():
            raise ValueError(
                "No name was supplied or one could not be determined.")

        logger.debug(
            "Checking to see if public key %s has been uploaded", fingerprint)

        public_key = self.get_key(fingerprint=fingerprint)
        if public_key:
            logger.debug("Key %s already exists, skipping.", fingerprint)
            return public_key

        logger.info("Uploading public key %s", path)
        response = self.post(
            self.URL + "/account/keys",
            data={
                "name": name,
                "public_key": open(path, "r").read()
            }
        )
        response.raise_for_status()
        data = response.json()
        return data["ssh_key"]

    def get_key(self, name=None, fingerprint=None):
        """
        Retrieves the public key matching the given name or fingerprint.
        """
        assert not all([name, fingerprint]), "Name or fingerprint only please"
        assert any([name, fingerprint]), "You must provide name or fingerprint"
        key_id = name or fingerprint
        logger.info("Trying to retrieve public key %s", key_id)
        response = self.get(self.URL + "/account/keys")
        response.raise_for_status()
        data = response.json()
        for key in data["ssh_keys"]:
            if key_id in (key["name"], key["fingerprint"]):
                return key
        logger.info(
            "No public key for search {'name': %r, 'fingerprint': %r}",
            name, fingerprint)

    def delete_key(self, name=None, fingerprint=None):
        key = self.get_key(name=name, fingerprint=fingerprint)
        if not key:
            return

        logger.warning("Deleting key %s", key["name"])
        response = self.delete(self.URL + "/account/keys/%d" % key["id"])
        response.raise_for_status()


class Droplets(Base):
    def __init__(self, search, ssh):
        super(Droplets, self).__init__()
        assert isinstance(search, Search)
        assert isinstance(ssh, SSH)
        self.search = search
        self.ssh = ssh

    def create_droplet(
            self, hostname, size, distribution=None, bootstrap=None,
            ssh_keys=None):
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

        data = {
            "name": hostname,
            "region": region_slug,
            "size": size,
            "image": distribution["id"],
        }

        if isinstance(ssh_keys, (str, int, dict)):
            ssh_keys = [ssh_keys]
        elif ssh_keys is None:
            ssh_keys = []

        droplet_keys = []

        for key in ssh_keys:
            if isinstance(key, int):
                droplet_keys.append(key)

            elif isinstance(key, str) and isfile(key):
                fingerprint, comment = self.ssh._get_fingerprint(key)
                remote_key = self.ssh.get_key(fingerprint=fingerprint)
                if remote_key is not None:
                    droplet_keys.append(fingerprint)
                else:
                    upload_key = self.ssh.upload_key(
                        name=hostname, path=key)
                    droplet_keys.append(upload_key["id"])

            elif isinstance(key, str):
                get_key = self.ssh.get_key(name=key)
                if get_key is None:
                    get_key = self.ssh.get_key(fingerprint=key)

                if get_key is None:
                    raise RuntimeError(
                        "Failed to find uploaded key %r", key)

                droplet_keys.append(get_key["id"])

            elif isinstance(key, dict):
                droplet_keys.append(key["id"])

            else:
                raise TypeError("Don't know how to handle %r here" % key)

        if droplet_keys:
            data.update(ssh_keys=droplet_keys)

        if bootstrap and isfile(bootstrap):
            bootstrap = open(bootstrap, "r").read()

        if bootstrap:
            data.update(user_data=bootstrap)

        logger.info(
            "Creating %s @ %s in %s (data: %s)",
            hostname, size, region_slug, pformat(data))

        response = self.post(
            self.URL + "/droplets", data=data
        )
        try:
            response.raise_for_status()
        except Exception:
            logger.error("Error in request: %s", pformat(response.json()))
            return

        droplet_id = response.json()["droplet"]["id"]

        logger.info("Waiting for droplet to become active")
        while True:
            response = self.get(self.URL + "/droplets/%d" % droplet_id)

            try:
                droplet_data = response.json()["droplet"]
            except KeyError:
                pass
            else:
                if droplet_data["status"] == "active":
                    return droplet_data

            time.sleep(10)

    def set_power_state(self, droplet_id, state):
        logger.info("Set power state of droplet %d to %s", droplet_id, state)
        if state is PowerState.ON:
            response = self.post(
                self.URL + "/droplets/%d/actions" % droplet_id,
                data={"type": "power_on"}
            )
            response.raise_for_status()
            return response.json()["action"]

        raise NotImplementedError(state)

    def delete_droplet(self, **fields):
        droplet = self.find_droplet(**fields)
        if not droplet:
            return

        logger.warning("Destroy droplet %d", droplet["id"])

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

    def get_droplet_ip(self, droplet, ip_version="v4"):
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
        logger.info("Searching for droplets matching %r", fields)
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

                if key == "ip" and value != self.get_droplet_ip(droplet):
                    break

            else:
                yield droplet

    def find_droplet(self, **fields):
        droplets = list(self.find_droplets(**fields))
        if not droplets:
            return
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



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


class BaseAPI(_Session):
    URL = NotImplemented

    def __init__(self):
        super().__init__()
        assert self.URL is not NotImplemented
        assert isinstance(self.URL, str)
        assert not self.URL.endswith("/")
        self.mount("https://", HTTPAdapter(max_retries=5))


class LinodeAPI(BaseAPI):
    URL = "https://api.linode.com"

    def _build_parameters(self, api_action, **kwargs):
        kwargs.setdefault("api_key", config.get(Providers.LINODE, "token"))
        kwargs.update(api_action=api_action)
        return kwargs

    def _process_response(self, response):
        response.raise_for_status()
        data = response.json()
        logger.debug("response: %r", data)
        if data["ERRORARRAY"]:
            raise RuntimeError(data["ERRORARRAY"])
        return data["DATA"]

    def _get_match(self, data, match_field, value, return_field=None):
        for entry in data:
            if entry[match_field] == value:
                if return_field is not None:
                    return entry[return_field]
                return entry
        else:
            raise ValueError("Failed to find anything matching %s" % value)

    def post(self, function, **kwargs):
        logger.debug("POST %s %r", function, kwargs)
        response = super().post(
            self.URL,
            params=self._build_parameters(function, **kwargs)
        )
        return self._process_response(response)

    def get(self, function, **kwargs):
        logger.debug("GET %s %r", function, kwargs)
        response = super().get(
            self.URL,
            params=self._build_parameters(function, **kwargs)
        )
        return self._process_response(response)

    def put(self, url, data=None, **kwargs):
        raise NotImplementedError

    def delete(self, url, **kwargs):
        raise NotImplementedError

    def get_plan_id(self, ram=None, cores=None):
        """Retrieves a plan ID based on the ram size"""
        assert ram or cores, "You must specify ram or cores"
        for entry in self.get("avail.linodeplans"):
            if entry["RAM"] == ram or entry["CORES"] == cores:
                return entry["PLANID"]
        else:
            raise ValueError(
                "No such plan matching query %r" % {"ram": ram, "cores": cores})

    def get_datacenter_id(self, abbreviation):
        """Returns the datacenter ID based on name"""
        return self._get_match(
            self.get("avail.datacenters"), "ABBR", abbreviation, "DATACENTERID")

    def get_kernel_id(self, label="Latest 64 bit (3.18.5-x86_64-linode52)"):
        """Returns the kernel id for the given name"""
        return self._get_match(
            self.get("avail.kernels"), "LABEL", label, "KERNELID")

    def get_image_id(self, label):
        """Returns the image id based on a the image label"""
        return self._get_match(
            self.get("image.list"), "LABEL", label, "IMAGEID")

    def get_domain_id(self, name):
        """Returns the domain id for the given domain name"""
        return self._get_match(
            self.get("domain.list"), "DOMAIN", name, "DOMAINID")

    def get_domain_resource_id(self, domain, record_type, name):
        domain_id = domain
        if isinstance(domain, str):
            domain_id = self.get_domain_id(domain)
        assert isinstance(domain_id, int)

        for entry in self.get("domain.resource.list", DomainID=domain_id):
            if entry["NAME"] == name and entry["TYPE"] == record_type:
                return entry["RESOURCEID"]
        else:
            raise ValueError(
                "No such domain record matching %r" % {
                    "domain": domain, "record_type": record_type, "name": name})


class DigitalOceanAPI(BaseAPI):
    URL = "https://api.digitalocean.com/v2"

    def __init__(self):
        super(DigitalOceanAPI, self).__init__()
        self.headers.update(
            Authorization="Bearer %s" % config.get(
                Providers.DIGITAL_OCEAN, "token"))

    def get_distribution(self, slug=None, regions=None):
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
            # get_regions() must be present in the distribution
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

    # TODO: support filtering on features too
    def get_regions(self, size, slug_prefix=None, features=None):
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

    def _get_ssh_fingerprint(self, path):
        bits, fingerprint, comment, typename = subprocess.check_output(
            ["ssh-keygen", "-lf", path]).strip().split()
        return fingerprint.decode("utf-8"), comment.decode("utf-8")

    def upload_public_ssh_key(self, path=None):
        if path is None:
            path = expanduser(config.get(Providers.DIGITAL_OCEAN, "public_key"))

        # Retrieve the signature of this key, we'll use this to determine
        # if the key has already been uploaded.
        assert isfile(path)
        fingerprint, comment = self._get_ssh_fingerprint(path)

        response = self.get(self.URL + "/account/keys")
        response.raise_for_status()
        data = response.json()

        logger.debug(
            "Checking to see if public key %s has been uploaded", fingerprint)
        for public_key in data["ssh_keys"]:
            if public_key["fingerprint"] == fingerprint:
                logger.debug("... key exists")
                break
        else:
            logger.info("Uploading public key %s", path)
            response = self.post(
                self.URL + "/account/keys",
                data={
                    "name": comment,
                    "public_key": open(path, "r").read()
                }
            )
            response.raise_for_status()

    def create_host(
            self, hostname, size, distribution=None, bootstrap=None):
        features = []
        if bootstrap:
            features.append("metadata")

        regions = self.get_regions(size, features=features)
        region_slugs = [region["slug"] for region in regions]
        distribution = self.get_distribution(
            slug=distribution, regions=region_slugs)

        # Get all regions which both get_regions() and get_distribution()
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

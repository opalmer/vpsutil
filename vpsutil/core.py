from requests import Session as _Session
from requests.adapters import HTTPAdapter

from vpsutil.config import Providers, config
from vpsutil.logger import logger


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
    URL = "https://api.digitalocean.com"


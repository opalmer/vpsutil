import argparse
from vpsutil.api import DigitalOcean
from vpsutil.logger import logger
from vpsutil.config import config, Providers
from vpsutil.ssh import SSHClient

try:
    from vpsutil_private import parser_hook
except ImportError:
    parser_hook = NotImplemented
    logger.warning(
        "parser_hook() will not be run, could not import vpsutil_private")


def destroy_resources(parser, args):
    logger.info("Destroying resources with the name %r", args.name)
    do = DigitalOcean()
    do.ssh.delete_key(name=args.name)

    do.droplets.delete_droplet(name=args.name)

    # For now we don't destroy any other record type
    for record_type in ("A", "AAAA"):
        do.dns.delete_record(args.domain, record_type, args.name)

    SSHClient.delete_rsa_key_pair(args.name)


def show_droplets(parser, args):
    do = DigitalOcean()
    droplets = list(do.droplets.find_droplets())

    print("Name             Address          Status Region Size")
    print("---------------- ---------------- ------ ------ --------")
    for droplet in droplets:
        ip_address = None
        for network in droplet["networks"]["v4"]:
            ip_address = network["ip_address"]
            break

        args = (
            droplet["name"], ip_address,
            droplet["status"], droplet["region"]["slug"],
            droplet["size_slug"]
        )
        print("{0:<16} {1:<16} {2:<6} {3:<6} {4:<8}".format(*args))


def ocean():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    parser.add_argument(
        "-d", "--domain",
        help="The domain you wish to operate on when working with DNS records",
        default=config.get(Providers.DIGITAL_OCEAN, "domain"))


    show = subparsers.add_parser("show", help="Show all droplets")
    show.set_defaults(func=show_droplets)

    destroy = subparsers.add_parser(
        "destroy", help="Destroy resources on Digital Ocean based on a name")
    destroy.add_argument("name", help="Name of objects to destroy")
    destroy.set_defaults(func=destroy_resources)

    if parser_hook is not NotImplemented:
        parser_hook(parser, subparsers)

    args = parser.parse_args()
    try:
        args.func
    except AttributeError:
        parser.error("No action provided")

    args.func(parser, args)
# vpsutil
A personal wrapper around some VPS APIs.  This mainly exists for 
experimentation and basic scripts.  There are few dependencies and only
one configuration file.

## Python Version
The code in this repository was written against Python 3.4.  It may
also work with Python 2.6+

# Setup
## Python
```console
> virtualenv -p python3.4 virtualenv  # -p is not required
> . virtualenv/bin/activate
> pip install -e .
```

## Configuration
The configuration for this library lives in `~/.vpsutil/config`.  It is highly
recommended that you secure the permissions of this directory so only you will 
be able to read it.

```dosini
[digital_ocean]
token = your_api_token_string
```

## Examples
### DNS
Create, update or delete DNS records.  This assumes the domain you are
attempting to manipulate already exits under your account.

```python
>>> from vpsutil.dns import LinodeDNSManager
>>> dns = LinodeDNSManager()
>>> dns.create_record("example.com", "A", "www", "127.0.0.1")
>>> dns.update_record("example.com", "A", "www", "")
>>> dns.delete_record("example.com", "A", "www")
```

### Command line Took Hook
The command tool contains a hook which allows for another module to reconfigure
or append commands to the parser before it runs.  To take advantage of this 
create a module named ``vpsutil_private`` with an importable callable 
named ``parser_hook``.  ``parser_hook`` should accept two input arguments
for the parser and subparser objects.  The ``parser_hook`` function could look
like:

```python
def deploy_vpn(args):
    pass
    

def parser_hook(parser, subparsers):
    parser.add_argument("foobar")
    
    deploy_vpn = subparser.add_parser("deploy_vpn")
    deploy_vpn.set_defaults(func=deploy_vpn_impl)
    
```

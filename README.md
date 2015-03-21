# vpsutil
A personal wrapper around a couple of VPS provider APIs.  This mainly exists
for experimentation and basic scripts.  There are few dependencies and only
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

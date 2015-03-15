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
> virtualenv -p python3.4 virtualenv
> . virtualenv/bin/activate
> pip install -e .
```

## Configuration
The configuration for this library lives in `~/.vpsutil`.  It is highly
recommended that you secure the permissions of this file so only you will be 
able to read it.

```dosini
[linode]
token = your_api_token_string

[digital_ocean]
token = your_api_token_string
```
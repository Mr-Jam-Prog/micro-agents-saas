import tomli
import tomli_w
import os

with open('pyproject.toml', 'rb') as f:
    config = tomli.load(f)

# Remove the fancy-pypi-readme hook if it's broken
if 'tool' in config and 'hatch' in config['tool'] and 'metadata' in config['tool']['hatch']:
    if 'hooks' in config['tool']['hatch']['metadata']:
        if 'fancy-pypi-readme' in config['tool']['hatch']['metadata']['hooks']:
            del config['tool']['hatch']['metadata']['hooks']['fancy-pypi-readme']

# Set standard dynamic readme if needed, but project already has readme = "README.md"
# Project also has dynamic = ["version"]

with open('pyproject.toml', 'wb') as f:
    tomli_w.dump(config, f)

import importlib
import sys
from django.conf import settings
from . import default_config

# 1. Populate current module namespace with defaults
for key in dir(default_config):
    if key.isupper():
        setattr(sys.modules[__name__], key, getattr(default_config, key))

# 2. Check for board/config.py and apply overrides
try:
    from . import config
    for key in dir(config):
        if key.isupper():
            setattr(sys.modules[__name__], key, getattr(config, key))
except ImportError:
    # config.py does not exist yet; defaults remain in effect
    pass

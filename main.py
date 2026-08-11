"""Entrypoint for the web-kernel and CLI tools (imperal validate/build).

Sets up sys.path, purges stale module cache, then imports ext/chat and all
handler modules so their decorators register on the same Extension instance
-- same pattern as the Asana Connector's main.py, needed because the
validator/kernel may load several extensions in one process and stale
cached modules would keep decorators registered on a dead Extension object.
"""

import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

_LOCAL = ("app", "models", "codes", "shared", "storage", "magnific_client",
          "model_registry", "model_discovery",
          "handlers", "handlers_discovery", "providers", "panels")
for _mod in _LOCAL:
    sys.modules.pop(_mod, None)

from app import ext, chat  # noqa: E402,F401
import handlers  # noqa: E402,F401
import handlers_discovery  # noqa: E402,F401
import providers  # noqa: E402,F401
import panels  # noqa: E402,F401

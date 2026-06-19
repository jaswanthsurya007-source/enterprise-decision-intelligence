"""EDIS platform SDK -- cross-cutting machinery shared by every service.

Exposes settings, structured logging, OpenTelemetry bootstrap, RFC 9457 error
types, the async tenant-scoped DB session, and the JWT/RBAC authz layer. The
event bus (``edis_platform.bus``) is added by F3 on top of the deps declared
here. Nothing in this package connects to a live resource at import time.
"""

from __future__ import annotations

from edis_platform.settings import Settings, get_settings

__version__ = "0.1.0"

__all__ = ["Settings", "get_settings", "__version__"]

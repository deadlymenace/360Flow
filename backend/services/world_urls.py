"""Accept only Marble HTTPS URLs and World Labs world ids.

Stored URLs are rendered into share pages. A value that is not a worldlabs.ai
HTTPS URL is dropped instead of being framed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_WORLD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def valid_world_id(value: object) -> str | None:
    if not isinstance(value, str) or not _WORLD_ID.fullmatch(value):
        return None
    return value


def valid_upload_url(value: object) -> str | None:
    """HTTPS upload target on World Labs or the Google storage host they return.

    A prepare_upload response is provider data, not a user URL, but it is still
    requested with the panorama bytes. Anything else is refused before the call.
    """
    if not isinstance(value, str) or any(char in value for char in ("\n", "\r", "\\")):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = (
        host == "worldlabs.ai"
        or host.endswith(".worldlabs.ai")
        or host == "storage.googleapis.com"
        or host.endswith(".storage.googleapis.com")
    )
    if not allowed or not parsed.path or parsed.path == "/":
        return None
    return value.strip()


def valid_marble_url(value: object) -> str | None:
    """HTTPS URL on worldlabs.ai or a subdomain, with no embedded credentials."""
    if not isinstance(value, str) or any(char in value for char in ("\n", "\r", "\\")):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "worldlabs.ai" and not host.endswith(".worldlabs.ai"):
        return None
    if not parsed.path or parsed.path == "/":
        return None
    return value.strip()

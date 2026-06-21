"""Bearer auth per contract §2.

If WEB_SEARCH_SECRET is empty the service is keyless and ignores
Authorization (a private/local SearXNG deployment). If a secret IS set, a
matching `Authorization: Bearer <secret>` is required; otherwise 401.
"""
from __future__ import annotations

from . import config


def is_authorized(authorization: str | None) -> bool:
    if not config.WEB_SEARCH_SECRET:
        return True  # keyless
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1].strip()
    return token == config.WEB_SEARCH_SECRET

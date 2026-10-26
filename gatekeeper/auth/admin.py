"""HTTP Basic auth guard for admin-only endpoints.

This is a deliberately simple, documented local-admin mechanism (a single
username/password pair from environment config) rather than a full
user-management/OAuth system, per the project scope.
"""

from __future__ import annotations

import hmac

from fastapi import Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from gatekeeper.errors import AdminAuthenticationError
from gatekeeper.settings import Settings, get_settings

_basic_scheme = HTTPBasic(auto_error=False)


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(_basic_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    if credentials is None:
        raise AdminAuthenticationError("Admin credentials required", headers={"WWW-Authenticate": "Basic"})
    user_ok = hmac.compare_digest(credentials.username, settings.admin_username)
    pass_ok = hmac.compare_digest(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise AdminAuthenticationError("Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

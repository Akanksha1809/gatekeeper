import pytest
from fastapi.security import HTTPBasicCredentials

from gatekeeper.auth.admin import require_admin
from gatekeeper.errors import AdminAuthenticationError
from gatekeeper.settings import Settings


def _settings():
    return Settings(admin_username="admin", admin_password="s3cret")


def test_valid_credentials_pass():
    creds = HTTPBasicCredentials(username="admin", password="s3cret")
    assert require_admin(creds, _settings()) == "admin"


def test_missing_credentials_rejected():
    with pytest.raises(AdminAuthenticationError):
        require_admin(None, _settings())


def test_wrong_password_rejected():
    creds = HTTPBasicCredentials(username="admin", password="wrong")
    with pytest.raises(AdminAuthenticationError):
        require_admin(creds, _settings())


def test_wrong_username_rejected():
    creds = HTTPBasicCredentials(username="nobody", password="s3cret")
    with pytest.raises(AdminAuthenticationError):
        require_admin(creds, _settings())

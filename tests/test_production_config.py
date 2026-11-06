"""Production mode must not start with unsafe defaults."""

import pytest
from pydantic import ValidationError

from src.core.config import Settings

SECRET = "x" * 40
SAFE = {
    "app_env": "production",
    "jwt_secret_key": SECRET,
    "allowed_hosts": "ops.example.com",
    "docs_enabled": False,
}


def make(**over):
    return Settings(_env_file=None, **{**SAFE, **over})


def test_safe_production_settings_are_accepted():
    assert make().allowed_host_list == ["ops.example.com"]


@pytest.mark.parametrize("hosts", ["*", "", " , "])
def test_production_rejects_wildcard_or_empty_hosts(hosts):
    with pytest.raises(ValidationError):
        make(allowed_hosts=hosts)


def test_production_rejects_enabled_api_docs():
    with pytest.raises(ValidationError):
        make(docs_enabled=True)


def test_production_rejects_debug():
    with pytest.raises(ValidationError):
        make(app_debug=True)


def test_development_keeps_convenient_defaults():
    settings = Settings(_env_file=None, app_env="development", jwt_secret_key=SECRET)
    assert settings.allowed_host_list == ["*"] and settings.docs_enabled is True

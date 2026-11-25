"""Startup validation and wiring for the external decision provider.

Two separate concerns are covered here.

*Configuration* must refuse an endpoint it cannot use, at boot. A scheme typo
found on the first advisory a human is waiting for is a worse failure than a
process that refuses to start.

*Selection* must be honest about the default. With no endpoint configured the
process uses the offline stub, and enabling the feature flag does not silently
reach the network. These tests pin both halves so a later refactor cannot turn
"decisioning on, no vendor configured" into outbound traffic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from notes_service.bootstrap.config import DatabaseProvider, Environment, Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": Environment.TESTING,
        "database_provider": DatabaseProvider.SQLITE,
        "database_url": "sqlite://",
        "log_json": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Configuration.
# --------------------------------------------------------------------------


def test_the_endpoint_defaults_to_none_so_no_vendor_is_contacted() -> None:
    settings = _settings()
    assert settings.decisioning_http_endpoint is None
    assert settings.decisioning_http_api_key is None


def test_a_blank_endpoint_is_treated_as_unset() -> None:
    settings = _settings(decisioning_http_endpoint="   ")
    assert settings.decisioning_http_endpoint is None


def test_an_https_endpoint_is_accepted() -> None:
    settings = _settings(decisioning_http_endpoint="https://judge.example.test/v1")
    assert settings.decisioning_http_endpoint == "https://judge.example.test/v1"


@pytest.mark.parametrize(
    "endpoint", ["http://judge.example.test/v1", "ftp://judge.example.test", "judge.example.test"]
)
def test_a_non_https_endpoint_is_rejected_at_startup(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="must be an https"):
        _settings(decisioning_http_endpoint=endpoint)


@pytest.mark.parametrize("timeout", [0, -1, 61, 3600])
def test_an_out_of_range_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValidationError, match="must be in"):
        _settings(decisioning_http_timeout_seconds=timeout)


def test_the_provider_name_defaults_to_an_identifier() -> None:
    assert _settings().decisioning_http_provider_name == "http"


# --------------------------------------------------------------------------
# Selection.
# --------------------------------------------------------------------------


def test_no_endpoint_selects_the_offline_stub_even_when_decisioning_is_enabled() -> None:
    """Enabling judgment without a vendor must not produce outbound traffic."""
    from notes_service.bootstrap.container import build_container

    settings = _settings(decisioning_enabled=True, decisioning_http_endpoint=None)
    container = build_container(settings)
    assert container.evaluate_decision().provider_name == "stub"


def test_a_configured_endpoint_selects_the_http_adapter() -> None:
    from notes_service.bootstrap.container import build_container

    settings = _settings(
        decisioning_http_endpoint="https://judge.example.test/v1",
        decisioning_http_provider_name="vendor",
    )
    container = build_container(settings)
    assert container.evaluate_decision().provider_name == "vendor"

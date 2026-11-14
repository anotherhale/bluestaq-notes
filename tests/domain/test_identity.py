"""Domain unit tests for identity value objects and entities."""

from __future__ import annotations

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.entities import ExternalIdentity, User
from notes_service.domain.identity.value_objects import (
    Email,
    ProviderSubject,
    UserId,
    UserStatus,
)


class TestEmail:
    def test_normalises_case_and_whitespace(self) -> None:
        assert Email("  Alice@Example.COM ") == "alice@example.com"

    @pytest.mark.parametrize("bad", ["", "not-an-email", "a@b", "@example.com", "a@.com"])
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            Email(bad)

    def test_domain_extracted(self) -> None:
        assert Email("alice@example.com").domain == "example.com"


class TestUserId:
    def test_parse_roundtrip(self) -> None:
        raw = "11111111-1111-1111-1111-111111111111"
        assert str(UserId.parse(raw)) == raw

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValidationError):
            UserId.parse("not-a-uuid")

    def test_new_is_unique(self) -> None:
        assert UserId.new() != UserId.new()


class TestUser:
    def test_register_defaults_active(self) -> None:
        user = User.register(email="x@example.com", display_name="X")
        assert user.status is UserStatus.ACTIVE
        assert user.is_active

    def test_display_name_required(self) -> None:
        with pytest.raises(InvariantViolation):
            User.register(email="x@example.com", display_name="   ")

    def test_suspended_user_cannot_authenticate(self) -> None:
        user = User.register(email="x@example.com", display_name="X", status=UserStatus.SUSPENDED)
        assert not user.is_active


class TestExternalIdentity:
    def test_link_exposes_provider_subject(self) -> None:
        identity = ExternalIdentity.link(user_id=UserId.new(), provider="demo", subject="abc")
        assert identity.provider == "demo"
        assert identity.subject == "abc"

    def test_provider_subject_requires_values(self) -> None:
        with pytest.raises(ValidationError):
            ProviderSubject(provider="", subject="x")
        with pytest.raises(ValidationError):
            ProviderSubject(provider="x", subject="")

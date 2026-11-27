"""Unit tests for the session and login-transaction domain aggregates.

These cover the security invariants directly, with no database and no HTTP:
single-use login transactions, PKCE derivation, and the two-clock session
lifetime model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from notes_service.domain.errors import InvariantViolation, ValidationError
from notes_service.domain.identity.session import (
    LOGIN_TRANSACTION_TTL,
    LoginTransaction,
    UserSession,
    generate_session_token,
    hash_session_token,
    pkce_challenge,
)
from notes_service.domain.identity.value_objects import UserId

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class TestLoginTransaction:
    def test_begin_generates_distinct_secrets(self) -> None:
        first = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)
        second = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)

        assert first.state != second.state
        assert first.nonce != second.nonce
        assert first.code_verifier != second.code_verifier

    def test_begin_sets_expiry_from_ttl(self) -> None:
        txn = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)

        assert txn.expires_at == T0 + LOGIN_TRANSACTION_TTL
        assert txn.is_expired(T0) is False

    def test_consume_is_single_use(self) -> None:
        txn = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)
        txn.consume(T0)

        assert txn.is_consumed
        with pytest.raises(InvariantViolation, match="already been used"):
            txn.consume(T0)

    def test_expired_transaction_cannot_be_consumed(self) -> None:
        txn = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)
        later = T0 + LOGIN_TRANSACTION_TTL + timedelta(seconds=1)

        with pytest.raises(InvariantViolation, match="expired"):
            txn.consume(later)

    def test_rejects_empty_redirect_uri(self) -> None:
        with pytest.raises(ValidationError, match="redirect_uri"):
            LoginTransaction.begin(redirect_uri="   ")

    def test_code_challenge_is_derived_from_verifier(self) -> None:
        txn = LoginTransaction.begin(redirect_uri="https://app.example/cb", now=T0)

        assert txn.code_challenge == pkce_challenge(txn.code_verifier)


class TestPkceChallenge:
    def test_matches_rfc7636_appendix_b_vector(self) -> None:
        # The canonical example from RFC 7636 Appendix B.
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

    def test_has_no_base64_padding(self) -> None:
        assert "=" not in pkce_challenge("a" * 43)


class TestUserSession:
    def _session(self, **overrides: object) -> UserSession:
        kwargs: dict[str, object] = {
            "user_id": UserId.new(),
            "provider": "oidc",
            "subject": "sub-123",
            "token": generate_session_token(),
            "idle_ttl": timedelta(minutes=30),
            "absolute_ttl": timedelta(hours=8),
            "now": T0,
        }
        kwargs.update(overrides)
        return UserSession.establish(**kwargs)  # type: ignore[arg-type]

    def test_stores_only_the_token_hash(self) -> None:
        token = generate_session_token()
        session = self._session(token=token)

        assert session.token_hash == hash_session_token(token)
        assert token not in session.token_hash

    def test_active_within_both_windows(self) -> None:
        session = self._session()
        assert session.is_active(T0 + timedelta(minutes=5)) is True

    def test_expires_when_idle_window_passes(self) -> None:
        session = self._session()
        assert session.is_active(T0 + timedelta(minutes=31)) is False

    def test_absolute_cap_overrides_activity(self) -> None:
        session = self._session(idle_ttl=timedelta(hours=1), absolute_ttl=timedelta(hours=2))

        # Keep touching the session: the idle clock never lapses, but the
        # absolute lifetime still ends the session.
        for minute in range(0, 130, 10):
            session.touch(T0 + timedelta(minutes=minute), idle_ttl=timedelta(hours=1))

        assert session.is_active(T0 + timedelta(hours=2, minutes=1)) is False

    def test_touch_never_extends_past_absolute_cap(self) -> None:
        session = self._session(idle_ttl=timedelta(hours=4), absolute_ttl=timedelta(hours=2))
        session.touch(T0 + timedelta(hours=1), idle_ttl=timedelta(hours=4))

        assert session.idle_expires_at == session.absolute_expires_at

    def test_touch_does_not_revive_inactive_session(self) -> None:
        session = self._session()
        late = T0 + timedelta(minutes=31)
        session.touch(late, idle_ttl=timedelta(minutes=30))

        assert session.last_seen_at == T0
        assert session.is_active(late) is False

    def test_revoke_is_immediate_and_idempotent(self) -> None:
        session = self._session()
        session.revoke(T0 + timedelta(minutes=1))
        session.revoke(T0 + timedelta(minutes=5))

        assert session.is_active(T0 + timedelta(minutes=2)) is False
        assert session.revoked_at == T0 + timedelta(minutes=1)

    def test_rejects_non_positive_ttls(self) -> None:
        with pytest.raises(ValidationError, match="idle_ttl"):
            self._session(idle_ttl=timedelta(0))
        with pytest.raises(ValidationError, match="absolute_ttl"):
            self._session(absolute_ttl=timedelta(seconds=-1))

    def test_tokens_are_unique_and_high_entropy(self) -> None:
        tokens = {generate_session_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(token) >= 43 for token in tokens)

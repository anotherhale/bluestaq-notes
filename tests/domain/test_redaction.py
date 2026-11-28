"""Domain unit tests for credential-shape redaction.

This module had no direct tests. Its behaviour was only observed through two
callers (the log formatter and the tool gateway's redaction obligation), so a
hole in the vocabulary was invisible unless a caller happened to exercise the
exact spelling. These tests bind the judgement itself.
"""

from __future__ import annotations

import pytest

from notes_service.domain.redaction import (
    REDACTED,
    is_sensitive_key,
    key_words,
    redact,
)


class TestKeyWords:
    def test_splits_snake_case(self) -> None:
        assert key_words("access_token") == ["access", "token"]

    def test_splits_camel_case(self) -> None:
        assert key_words("accessToken") == ["access", "token"]

    def test_splits_upper_snake(self) -> None:
        assert key_words("API_KEY") == ["api", "key"]

    def test_splits_on_punctuation(self) -> None:
        assert key_words("x-api-key") == ["x", "api", "key"]


class TestSpellingsReachOneVerdict:
    """The whole point of shape matching: one secret, many spellings."""

    @pytest.mark.parametrize(
        "key",
        [
            "secret",
            "client_secret",
            "clientSecret",
            "CLIENT_SECRET",
            "private_key",
            "x-api-key",
            "accessToken",
            "authorization",
            "bearer",
            "tokens",
            "credentials",
        ],
    )
    def test_credential_spellings_are_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key) is True


class TestOpaqueBearerCredentials:
    """Credentials whose *name* carries no obvious secret word."""

    @pytest.mark.parametrize(
        "key", ["jwt", "id_token", "access_token", "refresh_token", "auth_token"]
    )
    def test_token_shapes_are_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key) is True

    def test_bare_jwt_is_sensitive(self) -> None:
        # A JWT is presented as the credential itself; its dotted opaque shape
        # once made it pass every word test.
        assert is_sensitive_key("jwt") is True

    @pytest.mark.parametrize("key", ["code_verifier", "codeVerifier"])
    def test_pkce_verifier_is_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key) is True


class TestShortLivedCredentials:
    """A code with a short life still has a life."""

    @pytest.mark.parametrize("key", ["otp", "totp", "pin", "passcode", "one_time_code"])
    def test_one_time_codes_are_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key) is True


class TestWordPairPositionDoesNotMatter:
    """A sensitive pair is sensitive wherever it sits, not only at the end."""

    @pytest.mark.parametrize(
        "key",
        [
            "private_key_pem",
            "private_key_path",
            "access_key_id",
            "api_key_id",
            "api_key_header",
            "signing_key_arn",
            "encryption_key_version",
            "x_api_key_header",
            "aws_secret_access_key",
        ],
    )
    def test_qualified_credential_pairs_are_sensitive(self, key: str) -> None:
        # The predecessor matched only the trailing two words, so a trailing
        # qualifier (``pem``, ``id``, ``header``) decided the verdict and the
        # credential underneath it leaked. A qualifier is not a declassifier.
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "secretary",
            "keyboard",
            "key_note",
            "api_version",
            "private_note",
            "access_level",
            "signing_status",
        ],
    )
    def test_words_adjacent_to_a_credential_word_alone_are_not(self, key: str) -> None:
        # Widening the pair scan to every position must not turn a bare ``key``
        # or ``access`` into a secret: only the listed pairs are sensitive.
        assert is_sensitive_key(key) is False


class TestNonSensitiveKeysStayReadable:
    """Redaction that eats telemetry is redaction people route around."""

    @pytest.mark.parametrize(
        "key",
        [
            "request_id",
            "user_id",
            "path",
            "method",
            "status_code",
            "duration_ms",
            "content_type",
            "content_length",
            "idempotency_key",
            "partition_key",
            "session_id",
            "correlation_id",
        ],
    )
    def test_operational_keys_are_not_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key) is False

    def test_session_id_is_a_uuid_not_a_secret(self) -> None:
        # SessionId is a uuid.UUID correlation value; the cookie secret is stored
        # separately and hashed, so ``session_id`` must stay readable.
        assert is_sensitive_key("session_id") is False


class TestRedact:
    def test_replaces_value_with_marker_not_original(self) -> None:
        redacted = redact({"password": "hunter2"})
        assert redacted["password"] == REDACTED
        assert "hunter2" not in str(redacted)

    def test_recurses_into_nested_mappings(self) -> None:
        redacted = redact({"auth": {"jwt": "eyJ.a.b"}})
        assert redacted == {"auth": {"jwt": REDACTED}}

    def test_whole_list_under_credential_key_goes(self) -> None:
        # Replacing elements one at a time would keep the count and order of a
        # secret set.
        redacted = redact({"tokens": ["a", "b", "c"]})
        assert redacted["tokens"] == REDACTED

    def test_recurses_into_lists_of_mappings(self) -> None:
        redacted = redact({"items": [{"otp": "123456"}, {"note": "keep"}]})
        assert redacted == {"items": [{"otp": REDACTED}, {"note": "keep"}]}

    def test_does_not_mutate_the_input(self) -> None:
        original = {"password": "hunter2", "nested": {"pin": "4321"}}
        redact(original)
        assert original == {"password": "hunter2", "nested": {"pin": "4321"}}

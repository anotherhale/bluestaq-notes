"""Direct tests for the real OIDC ID-token verifier.

Every other login test injects ``FakeRelyingParty``, so assertions about a wrong
issuer, audience, expiry, or nonce were previously made against the fake. This
file exercises ``HttpOidcRelyingParty._verify_id_token`` itself, which is the only
code that checks a token's signature, ``iss``, ``aud``, ``exp``/``iat``, and
nonce.

Keys are generated in-process and the JWKS lookup is the only collaborator that is
replaced (it is the one thing that would otherwise touch a remote server).
Signing, decoding, and every claim check are the production code paths.

Each rejection test is written so that removing the corresponding ``jwt.decode``
option, or inverting the nonce comparison, makes it fail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from notes_service.application.errors import AuthenticationError
from notes_service.application.ports.oidc import VerifiedIdToken
from notes_service.domain.identity.assurance import (
    STEP_UP_FLOOR,
    AuthenticationAssurance,
)
from notes_service.infrastructure.identity.oidc_relying_party import HttpOidcRelyingParty

ISSUER = "https://idp.example.com"
CLIENT_ID = "notes-client"
SUBJECT = "subject-alice"
NONCE = "nonce-abc123"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    """A second key: valid tokens are signed with ``signing_key``, not this one."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StaticJwks:
    """A JWKS client that always hands back one fixed public key."""

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        self._public = key.public_key()

    def get_signing_key_from_jwt(self, _token: str) -> object:
        return type("SigningKey", (), {"key": self._public})()


def _relying_party(jwks_key: rsa.RSAPrivateKey) -> HttpOidcRelyingParty:
    party = HttpOidcRelyingParty(
        provider_name="test-idp",
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="secret",
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        jwks_url=f"{ISSUER}/jwks",
        redirect_uri="https://app.example.com/callback",
    )
    party._jwks_client = _StaticJwks(jwks_key)  # type: ignore[assignment]
    return party


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": SUBJECT,
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "nonce": NONCE,
        "email": "alice@example.com",
        "name": "Alice",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def _token(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    return jwt.encode(_claims(**overrides), key, algorithm="RS256")


# ---------------------------------------------------------------------------
# NEX-058 item 7: a fail-closed step-up refusal must be diagnosable.
#
# A single-factor login is not an error — it is a recorded fact that will make a
# step-up policy refuse. The refusal happens much later, at the tool gateway,
# where the claims no longer exist. Without a line here an operator sees only
# "step-up refused" and cannot tell a misconfigured IdP from a weak login. The
# line must name the failing *stage* and must never echo a claim value.
# ---------------------------------------------------------------------------

_ASSURANCE_LOGGER = "notes_service.identity.oidc"


def _assurance_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _ASSURANCE_LOGGER and "assurance" in record.getMessage()
    ]


def test_single_factor_login_logs_the_failing_claim_stage(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    # The classic surprise: two recognised methods, but both are one-time codes,
    # so the login is single-factor.
    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr=None, amr=["otp", "totp"]), expected_nonce=NONCE
    )

    records = _assurance_records(caplog)
    assert len(records) == 1
    assert "amr=recognised" in records[0]
    assert "amr-families=1" in records[0]
    assert "test-idp" in records[0]


def test_single_factor_login_names_an_absent_claim_as_absent(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr=None, amr=None), expected_nonce=NONCE
    )

    records = _assurance_records(caplog)
    assert len(records) == 1
    assert "acr=absent" in records[0]
    assert "amr=absent" in records[0]


def test_single_factor_login_flags_unrecognised_methods(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    # A provider using vocabulary this build does not model can only understate;
    # the log must point at the vocabulary, not at the account.
    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr=None, amr=["x509", "u2f"]), expected_nonce=NONCE
    )

    records = _assurance_records(caplog)
    assert len(records) == 1
    assert "amr=unrecognised-methods" in records[0]


def test_single_factor_login_names_a_vetoed_acr_as_negated(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    # ``mfa-optional`` names multi-factor only to deny it. The diagnostic must say
    # so, because "negated" is a fixable IdP configuration while "unrecognised"
    # suggests the wrong vocabulary.
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr="mfa-optional", amr=None), expected_nonce=NONCE
    )

    records = _assurance_records(caplog)
    assert len(records) == 1
    assert "acr=negated" in records[0]


def test_a_live_marker_after_a_vetoed_one_still_satisfies_the_floor(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    # Guards the shared marker scan: a veto applies only to the marker it is
    # adjacent to, so a later live marker must still count. Reading this as
    # single-factor would be a regression in enforcement, not just in logging.
    # (``mfa-optional:multi-factor`` does NOT work here - ``optional`` is adjacent
    # to both markers and correctly vetoes both.)
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr="mfa-not-required:multi:factor", amr=None),
        expected_nonce=NONCE,
    )

    assert verified.assurance.meets(STEP_UP_FLOOR)
    assert _assurance_records(caplog) == []


def test_multi_factor_login_logs_no_assurance_diagnostic(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    # The line exists to explain a refusal. A login that satisfies the floor must
    # not emit it, or the signal drowns in noise on every normal MFA login.
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)

    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr="urn:acr:mfa", amr=["pwd", "hwk"]), expected_nonce=NONCE
    )

    assert _assurance_records(caplog) == []


def test_assurance_diagnostic_never_echoes_a_claim_value(
    signing_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    # ``acr`` is an IdP-chosen class and ``amr`` can name account-specific
    # methods, so the diagnostic must describe the shape only. Both values below
    # are distinctive so their absence from the log is meaningful.
    caplog.set_level("INFO", logger=_ASSURANCE_LOGGER)
    acr_value = "urn:example:acr:distinctive-9f3a"
    amr_value = "distinctive-method-7c1b"

    _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr=acr_value, amr=[amr_value]), expected_nonce=NONCE
    )

    records = _assurance_records(caplog)
    assert len(records) == 1
    assert acr_value not in records[0]
    assert amr_value not in records[0]
    assert "acr=unrecognised" in records[0]
    assert "amr=unrecognised-methods" in records[0]


def test_valid_token_yields_verified_claims(signing_key: rsa.RSAPrivateKey) -> None:
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key), expected_nonce=NONCE
    )

    assert isinstance(verified, VerifiedIdToken)
    assert verified.subject == SUBJECT
    assert verified.issuer == ISSUER
    assert verified.audience == CLIENT_ID
    assert verified.nonce == NONCE
    assert verified.email == "alice@example.com"
    assert verified.name == "Alice"
    assert verified.expires_at > verified.issued_at


def test_optional_claims_absent_are_reported_as_none(signing_key: rsa.RSAPrivateKey) -> None:
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key, email=None, name=None), expected_nonce=NONCE
    )

    assert verified.email is None
    assert verified.name is None


def test_token_without_expiry_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # PyJWT only enforces "exp" when it is present, so a token that simply omits it
    # would otherwise never expire. Pins the required-claims option.
    token = _token(signing_key, exp=None)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_token_without_issued_at_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = _token(signing_key, iat=None)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_token_without_issuer_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = _token(signing_key, iss=None)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_token_without_audience_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = _token(signing_key, aud=None)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_token_with_blank_subject_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = _token(signing_key, sub="   ")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_token_signed_by_another_key_is_rejected(
    signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
) -> None:
    # Pins verify_signature: without it the attacker-chosen key would be accepted.
    token = _token(other_key)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_wrong_issuer_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Pins issuer=...: a token minted by a different IdP must not authenticate.
    token = _token(signing_key, iss="https://evil.example.com")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_wrong_audience_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Pins audience=...: a token issued for another client must not authenticate.
    token = _token(signing_key, aud="some-other-client")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_expired_token_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Pins verify_exp, with leeway far exceeded so the default 30s cannot mask it.
    past = datetime.now(UTC) - timedelta(hours=1)
    token = _token(
        signing_key,
        iat=int((past - timedelta(minutes=5)).timestamp()),
        exp=int(past.timestamp()),
    )

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_future_issued_at_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Pins verify_iat: a token from the future is a clock/forgery signal.
    future = datetime.now(UTC) + timedelta(hours=1)
    token = _token(
        signing_key,
        iat=int(future.timestamp()),
        exp=int((future + timedelta(minutes=5)).timestamp()),
    )

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_nonce_mismatch_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Pins the nonce comparison: a replayed token from another login must fail.
    token = _token(signing_key, nonce="a-different-nonce")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_absent_nonce_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # A token with no nonce cannot be tied to this login transaction.
    token = _token(signing_key, nonce=None)

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_non_ascii_nonce_is_a_refusal_not_a_crash(signing_key: rsa.RSAPrivateKey) -> None:
    # The nonce claim is untrusted input from the IdP response. secrets.compare_digest
    # raises TypeError on non-ASCII str, which would surface as a 500 at the edge.
    token = _token(signing_key, nonce="nonce-café")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "iss", "aud"])
def test_required_claims_must_be_present(signing_key: rsa.RSAPrivateKey, claim: str) -> None:
    # Pins options={"require": [...]}: a token missing a standard claim is invalid.
    token = _token(signing_key, **{claim: None})

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_blank_subject_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    token = _token(signing_key, sub="   ")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_unsigned_alg_none_token_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # Algorithm confusion: "alg: none" must never be accepted as verified.
    token = jwt.encode(_claims(), key="", algorithm="none")

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_hs256_token_signed_with_public_key_is_rejected(signing_key: rsa.RSAPrivateKey) -> None:
    # The classic confusion attack: forge an HS256 token using the public key PEM as
    # the HMAC secret, so a verifier that trusted the token's own "alg" header would
    # accept an attacker-minted token. PyJWT refuses to *sign* this, so it is built
    # by hand; only the configured RS256 allowlist stops it at verification.
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(_claims()).encode())
    public_pem = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = hmac.new(public_pem, f"{header}.{body}".encode(), hashlib.sha256).digest()
    token = f"{header}.{body}.{_b64url(signature)}"

    with pytest.raises(AuthenticationError):
        _relying_party(signing_key)._verify_id_token(token, expected_nonce=NONCE)


def test_construction_refuses_unsafe_algorithms() -> None:
    for algorithm in ("none", "HS256"):
        with pytest.raises(ValueError):
            HttpOidcRelyingParty(
                provider_name="test-idp",
                issuer=ISSUER,
                client_id=CLIENT_ID,
                client_secret=None,
                authorization_endpoint=f"{ISSUER}/authorize",
                token_endpoint=f"{ISSUER}/token",
                jwks_url=f"{ISSUER}/jwks",
                redirect_uri="https://app.example.com/callback",
                algorithms=(algorithm,),
            )


def test_authorization_url_carries_pkce_and_nonce() -> None:
    party = _relying_party(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    state = uuid.uuid4().hex

    url = party.authorization_url(
        state=state, nonce=NONCE, code_challenge="challenge", login_hint="alice@example.com"
    )

    assert url.startswith(f"{ISSUER}/authorize?")
    assert f"state={state}" in url
    assert f"nonce={NONCE}" in url
    assert "code_challenge_method=S256" in url
    assert f"client_id={CLIENT_ID}" in url
    assert "login_hint=alice%40example.com" in url


def test_verified_token_carries_multi_factor_assurance(signing_key: rsa.RSAPrivateKey) -> None:
    # The mapping is consulted on the real verification path, not merely correct in
    # isolation: a stepped-up login must reach the caller as multi-factor, or the
    # step-up obligation would refuse a human who actually satisfied it.
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr="urn:acme:multi-factor"), expected_nonce=NONCE
    )

    assert verified.assurance is AuthenticationAssurance.MULTI_FACTOR
    assert verified.assurance.meets(STEP_UP_FLOOR)


def test_verified_token_carries_amr_derived_assurance(signing_key: rsa.RSAPrivateKey) -> None:
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key, amr=["pwd", "otp"]), expected_nonce=NONCE
    )

    assert verified.assurance is AuthenticationAssurance.MULTI_FACTOR


def test_verified_token_without_assurance_claims_is_single_factor(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # The fail-closed default must survive the whole path, which is where it
    # matters: a token carrying no acr/amr must not yield step-up authority.
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key), expected_nonce=NONCE
    )

    assert verified.assurance is AuthenticationAssurance.SINGLE_FACTOR
    assert not verified.assurance.meets(STEP_UP_FLOOR)


def test_verified_token_with_negated_acr_is_single_factor(signing_key: rsa.RSAPrivateKey) -> None:
    # Regression guard for the fail-open this slice fixed: a token whose acr names
    # multi-factor in order to deny it must not arrive as multi-factor.
    verified = _relying_party(signing_key)._verify_id_token(
        _token(signing_key, acr="urn:acme:mfa-not-required"), expected_nonce=NONCE
    )

    assert verified.assurance is AuthenticationAssurance.SINGLE_FACTOR

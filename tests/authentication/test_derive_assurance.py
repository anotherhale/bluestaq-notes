"""Direct tests for ``derive_assurance`` — the signal ``step-up`` rests on.

ADR-015 makes the ``step-up`` obligation enforceable by naming one deterministic
fact: the recorded authentication assurance of the delegating human. That fact is
minted here, by mapping the IdP's ``acr``/``amr`` claims onto a closed vocabulary.
Nothing else in the platform derives it, so a defect in this function is not a
local bug — it is the control itself reading the wrong value.

Every test below is written so that inverting the corresponding branch makes it
fail. The failure mode this file exists to prevent is the *fail-open* one: a claim
shape the IdP sends, that this function does not recognise, being read as
multi-factor. An unrecognised value must degrade to the weakest level, because an
unpersisted or unmodelled value that reads as authority is exactly what ADR-015
forbids.
"""

from __future__ import annotations

from typing import Any

import pytest

from notes_service.domain.identity.assurance import (
    STEP_UP_FLOOR,
    AuthenticationAssurance,
)
from notes_service.infrastructure.identity.oidc_relying_party import derive_assurance

MULTI = AuthenticationAssurance.MULTI_FACTOR
SINGLE = AuthenticationAssurance.SINGLE_FACTOR


def _derive(**claims: Any) -> AuthenticationAssurance:
    return derive_assurance(dict(claims))


# ---------------------------------------------------------------------------
# The default: absent, empty, or malformed claims read as single-factor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({}, id="no-claims-at-all"),
        pytest.param({"acr": None, "amr": None}, id="explicit-nulls"),
        pytest.param({"acr": "", "amr": []}, id="empty-values"),
        pytest.param({"acr": "   ", "amr": ""}, id="whitespace-only"),
        pytest.param({"acr": 123, "amr": 456}, id="non-string-scalars"),
        pytest.param({"acr": {"level": "mfa"}, "amr": {"methods": ["pwd", "mfa"]}}, id="dicts"),
        pytest.param({"acr": ["mfa"], "amr": None}, id="acr-as-list"),
    ],
)
def test_unrecognised_claim_shapes_read_as_single_factor(claims: dict[str, Any]) -> None:
    # The core fail-closed property. A provider that sends a shape this build does
    # not model must not be read as stronger than it is.
    assert derive_assurance(claims) is SINGLE


def test_no_claims_does_not_satisfy_the_step_up_floor() -> None:
    # Ties the default to the consequence: a token carrying no assurance evidence
    # cannot be used to spend a step-up obligation.
    assert not derive_assurance({}).meets(STEP_UP_FLOOR)


# ---------------------------------------------------------------------------
# ``acr``: a recognised multi-factor level names the class of login performed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "acr",
    [
        pytest.param("mfa", id="bare-mfa"),
        pytest.param("MFA", id="uppercase"),
        pytest.param("  mfa  ", id="surrounding-whitespace"),
        pytest.param("urn:acme:multi-factor", id="urn-multi-factor"),
        pytest.param("urn:acme:multifactor", id="urn-multifactor"),
        pytest.param("urn:acme:authentication:mfa", id="urn-trailing-mfa"),
        pytest.param("https://idp.example.com/loa/mfa", id="https-path-mfa"),
        pytest.param("urn:acme:pwd+mfa", id="plus-separated"),
        pytest.param("urn:acme:mfa:gold", id="mfa-as-leading-segment"),
    ],
)
def test_recognised_acr_levels_read_as_multi_factor(acr: str) -> None:
    assert derive_assurance({"acr": acr}) is MULTI


@pytest.mark.parametrize(
    "acr",
    [
        pytest.param("urn:mace:incommon:iap:silver", id="incommon-silver"),
        pytest.param("urn:acme:loa:1", id="numeric-loa-one"),
        pytest.param("urn:acme:loa:0", id="numeric-loa-zero"),
        pytest.param("https://idp.example.com/loa/1", id="https-loa-one"),
        pytest.param("urn:acme:password", id="password-class"),
        pytest.param("urn:acme:pwd", id="pwd-class"),
        pytest.param("ial1", id="ial-one"),
    ],
)
def test_acr_levels_that_do_not_name_multi_factor_read_as_single(acr: str) -> None:
    assert derive_assurance({"acr": acr}) is SINGLE


@pytest.mark.parametrize(
    "acr",
    [
        pytest.param("https://idp.example.com/mfa-optional", id="mfa-optional"),
        pytest.param("urn:acme:mfa-not-required", id="mfa-not-required"),
        pytest.param("urn:acme:mfa-disabled", id="mfa-disabled"),
        pytest.param("urn:acme:nomfa", id="nomfa"),
        pytest.param("urn:acme:not-multifactor", id="not-multifactor"),
        pytest.param("urn:acme:no-mfa", id="no-mfa"),
        pytest.param("urn:acme:mfa-without-otp", id="mfa-without"),
        pytest.param("urn:acme:multi-factor-optional", id="multi-factor-optional"),
        pytest.param("urn:acme:mfa:not-sms", id="negation-after-marker"),
        pytest.param("urn:acme:not-multi-factor", id="negated-multi-factor"),
    ],
)
def test_acr_naming_multi_factor_to_deny_it_does_not_read_as_multi_factor(acr: str) -> None:
    # A marker must be a positive assertion, not a substring. ``mfa-optional`` and
    # ``mfa-not-required`` name multi-factor in order to say it was *not*
    # performed; a substring match read every one of them as stepped up, which
    # would let a weaker login spend a step-up obligation. That was the shipped
    # behaviour until this slice, so these cases pin the correction.
    assert derive_assurance({"acr": acr}) is SINGLE


def test_negation_vetoes_the_marker_on_either_side() -> None:
    # Both directions of adjacency, stated once each so the veto cannot be
    # implemented for only one side and still pass the parametrised cases.
    assert derive_assurance({"acr": "urn:acme:optional-mfa"}) is SINGLE
    assert derive_assurance({"acr": "urn:acme:mfa-optional"}) is SINGLE
    assert derive_assurance({"acr": "urn:acme:optional-mfa-required"}) is SINGLE


# ---------------------------------------------------------------------------
# ``amr``: a second, independent method alongside a first one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amr",
    [
        pytest.param(["pwd", "mfa"], id="pwd-and-mfa"),
        pytest.param(["pwd", "otp"], id="pwd-and-otp"),
        pytest.param(["pwd", "totp"], id="pwd-and-totp"),
        pytest.param(["pwd", "hwk"], id="pwd-and-hardware-key"),
        pytest.param(["pwd", "webauthn"], id="pwd-and-webauthn"),
        pytest.param(["pwd", "fido"], id="pwd-and-fido"),
        pytest.param(["pwd", "passkey"], id="pwd-and-passkey"),
        pytest.param(["pwd", "sms"], id="pwd-and-sms"),
        pytest.param(["pwd", "fpt"], id="pwd-and-fingerprint"),
        pytest.param(["PWD", "MFA"], id="uppercase"),
        pytest.param([" pwd ", " mfa "], id="untrimmed"),
        pytest.param(("pwd", "mfa"), id="tuple"),
    ],
)
def test_second_independent_method_reads_as_multi_factor(amr: object) -> None:
    assert derive_assurance({"amr": amr}) is MULTI


@pytest.mark.parametrize(
    "amr",
    [
        pytest.param(["pwd"], id="single-method"),
        pytest.param("pwd", id="single-method-as-string"),
        pytest.param(["pwd", "pwd"], id="same-method-twice"),
        pytest.param(["pwd", "password"], id="two-first-factor-spellings"),
        pytest.param(["mfa"], id="mfa-alone"),
        pytest.param(["otp"], id="otp-alone"),
        pytest.param(["pwd", "unknown-method"], id="unrecognised-second-method"),
        pytest.param(["pwd", "mfa2"], id="unrecognised-mfa-spelling"),
        pytest.param([], id="empty-list"),
        pytest.param([1, 2], id="non-string-items"),
        pytest.param(["pwd", 123], id="mixed-items"),
    ],
)
def test_amr_without_a_recognised_second_factor_reads_as_single(amr: object) -> None:
    # ``["mfa"]`` alone is single-factor on purpose: the documented contract is a
    # recognised second factor *alongside a first one*. Understating is the safe
    # direction — it refuses a step-up rather than granting one it cannot justify.
    assert derive_assurance({"amr": amr}) is SINGLE


def test_amr_as_a_string_is_one_method_not_a_character_bag() -> None:
    # A bare string must not be split into characters or words: "pwd mfa" as one
    # string is one unrecognised method, not two methods that happen to include
    # a second factor.
    assert derive_assurance({"amr": "pwd mfa"}) is SINGLE


# ---------------------------------------------------------------------------
# ``amr`` counts factor *families*, not method names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amr",
    [
        pytest.param(["otp", "totp"], id="otp-and-totp"),
        pytest.param(["sms", "tel"], id="sms-and-tel"),
        pytest.param(["pwd", "pin"], id="password-and-pin"),
        pytest.param(["hwk", "swk"], id="hardware-and-software-key"),
        pytest.param(["webauthn", "passkey"], id="webauthn-and-passkey"),
        pytest.param(["face", "iris"], id="face-and-iris"),
        pytest.param(["otp", "totp", "hotp"], id="three-methods-one-family"),
        pytest.param(["pwd", "password", "pin"], id="three-knowledge-spellings"),
        pytest.param(["fpt", "face", "iris", "vbm"], id="four-biometric-spellings"),
    ],
)
def test_amr_methods_from_one_factor_family_read_as_single(amr: list[str]) -> None:
    # The defect this pins: counting distinct method names read two one-time
    # codes, or two text messages, as two independent factors. A second factor
    # has to come from a different family; anything else is one factor and must
    # refuse a step-up rather than spend one.
    assert derive_assurance({"amr": amr}) is SINGLE


@pytest.mark.parametrize(
    "amr",
    [
        pytest.param(["pwd", "otp"], id="knowledge-and-one-time-code"),
        pytest.param(["pwd", "sms"], id="knowledge-and-sms"),
        pytest.param(["pwd", "fpt"], id="knowledge-and-biometric"),
        pytest.param(["pwd", "hwk"], id="knowledge-and-hardware-key"),
        pytest.param(["otp", "fpt"], id="one-time-code-and-biometric"),
        pytest.param(["pin", "hwk"], id="pin-and-hardware-key"),
        pytest.param(["pwd", "mfa"], id="knowledge-and-generic-mfa"),
    ],
)
def test_amr_methods_from_independent_families_read_as_multi_factor(amr: list[str]) -> None:
    # The positive direction: distinct families still grant multi-factor, so the
    # family rule narrows the defect without breaking a genuine second factor.
    assert derive_assurance({"amr": amr}) is MULTI


# ---------------------------------------------------------------------------
# Both signals together, and one that must not rescue the other.
# ---------------------------------------------------------------------------


def test_acr_alone_is_sufficient() -> None:
    assert derive_assurance({"acr": "urn:acme:mfa"}) is MULTI


def test_amr_alone_is_sufficient() -> None:
    assert derive_assurance({"amr": ["pwd", "otp"]}) is MULTI


def test_weak_acr_is_not_rescued_by_a_weak_amr() -> None:
    # Neither signal says multi-factor, so the result is single-factor. Guards
    # against a future "either claim present" shortcut.
    assert derive_assurance({"acr": "urn:acme:loa:1", "amr": ["pwd"]}) is SINGLE


def test_strong_amr_wins_over_a_weak_acr() -> None:
    # The docstring says either signal is sufficient; a provider that reports a
    # low acr alongside a genuine second factor must not be understated into
    # denying a step-up the human actually performed.
    assert derive_assurance({"acr": "urn:acme:loa:1", "amr": ["pwd", "otp"]}) is MULTI


# ---------------------------------------------------------------------------
# Numeric levels are deliberately unmapped.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "acr",
    [
        pytest.param("aal2", id="nist-aal2"),
        pytest.param("urn:nist:800-63:aal2", id="nist-urn-aal2"),
        pytest.param("loa3", id="loa3"),
        pytest.param("urn:okta:loa:2", id="okta-loa-2"),
        pytest.param("ial2", id="ial2-identity-proofing"),
    ],
)
def test_numeric_level_conventions_are_not_read_as_multi_factor(acr: str) -> None:
    # These describe a level whose meaning differs by provider: ``ial2`` is
    # identity proofing, not a factor count, and ``aal2`` is a NIST category
    # several providers reach with one factor. Mapping any of them would be a
    # guess inside an enforcement path, so they read as single-factor and refuse
    # a step-up rather than granting one. Mapping them needs an ADR.
    assert derive_assurance({"acr": acr}) is SINGLE

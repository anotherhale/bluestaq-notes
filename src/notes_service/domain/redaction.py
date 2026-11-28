"""Shape-based redaction of credential-bearing values.

Two callers need the same judgement about what counts as a secret: the log
formatter, which must not write a credential into a log line, and the tool
gateway's ``redaction`` obligation (ADR-014), which must not return one to a
caller. Keeping two copies of that judgement is how they drift — one gets a new
sensitive word and the other silently keeps leaking it. This module is the single
implementation; the log formatter imports it rather than restating it.

The verdict is reached from the *shape* of a key, not from a list of exact
spellings. An exact-string matcher only protects the spellings someone thought
of: ``client_secret``, ``private_key``, ``x-api-key``, ``accessToken`` and
``session_cookie`` are all the same disclosure and none of them equals
``secret``, ``key`` or ``token``. Splitting a key into words and testing each
word is what makes those reach the same verdict.

Deliberately pure and framework-free: this lives in the domain because both the
governance obligation and the logging adapter depend on it, and the domain is
the only layer both are allowed to import.
"""

from __future__ import annotations

from typing import Any

REDACTED = "<redacted>"

# Words that name a credential on their own, in any position and in any
# combination with other words. Matched against the *segments* of a key, not
# against the whole key: ``client_secret``, ``accessToken`` and ``tokens`` are
# the same disclosure as ``secret``, ``token`` and ``token``.
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "cookie",
        "credential",
        "authorization",
        "bearer",
        "apikey",
        # A JSON Web Token is a bearer credential in its own right: it is
        # presented *as* the credential, so a JWT in a log line is a session
        # anyone holding the line can replay. Its opaque dotted shape made it
        # pass every word test above.
        "jwt",
        # One-time codes and PINs are credentials with a short life, not with no
        # life. ``otp`` and ``pin`` are named as authentication factors in the
        # OIDC ``amr`` vocabulary, and a code still valid when the log is read
        # is a code an attacker can use.
        "otp",
        "totp",
        "pin",
        "passcode",
    }
)

# Pairs where neither word is sensitive alone but the combination is. ``key`` on
# its own is ordinary (``idempotency_key``, ``partition_key``); ``private_key``
# and ``api_key`` are not.
_SENSITIVE_WORD_PAIRS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("access", "key"),
        ("secret", "key"),
        ("signing", "key"),
        ("encryption", "key"),
        # The PKCE ``code_verifier`` is the secret half of the pair whose hash is
        # sent as ``code_challenge``; holding it lets a captured authorization
        # code be redeemed. Neither word is sensitive alone, so this needs to be
        # a pair, not a word.
        ("code", "verifier"),
        # ``one_time_code`` splits to ``one / time / code``; the pair test reads the
        # trailing two words, so this covers the spelled-out form of ``otp``.
        ("time", "code"),
    }
)

# ``content`` holds subject material, but the words that routinely accompany it
# in a *descriptor* do not. ``content_type`` and ``content_length`` describe a
# payload; ``content`` and ``note_content`` are the payload.
_CONTENT_WORD = "content"
_CONTENT_DESCRIPTOR_WORDS = frozenset({"type", "length", "encoding", "hash", "size"})


def key_words(key: str) -> list[str]:
    """Split a key into lowercase words across separators and camelCase.

    ``accessToken``, ``access_token`` and ``ACCESS-TOKEN`` must all reach the
    same verdict, because a caller picks the spelling without thinking about
    redaction and a matcher that only knows one spelling is a matcher with a
    hole.
    """
    words: list[str] = []
    current = ""
    previous_lower = False
    for char in key:
        if char.isalnum():
            if char.isupper() and previous_lower:
                if current:
                    words.append(current)
                current = char.lower()
            else:
                current += char.lower()
            previous_lower = char.islower() or char.isdigit()
        else:
            if current:
                words.append(current)
            current = ""
            previous_lower = False
    if current:
        words.append(current)
    return words


def is_sensitive_key(key: str) -> bool:
    """Whether a key names a credential, by shape rather than by exact spelling.

    The predecessor of this function was a set of eleven exact strings. Every
    credential spelled differently — ``client_secret``, ``private_key``,
    ``x-api-key``, ``accessToken``, ``session_cookie`` — passed through it
    untouched, so the module's "sensitive data must never be logged" was
    enforced only for the spellings someone had happened to list. That is the
    same "enumerate what you know, treat the unknown as safe" shape as the
    six-key log allowlist this replaced.
    """
    words = key_words(key)
    if not words:
        return False
    for word in words:
        if word in _SENSITIVE_WORDS:
            return True
        # A plural is the same secret: ``tokens``, ``cookies``, ``credentials``.
        if word.endswith("s") and word[:-1] in _SENSITIVE_WORDS:
            return True
    if len(words) >= 2 and tuple(words[-2:]) in _SENSITIVE_WORD_PAIRS:
        return True
    return _CONTENT_WORD in words and not _CONTENT_DESCRIPTOR_WORDS.intersection(words)


def redact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``mapping`` with sensitive values replaced.

    Recurses into nested mappings and sequences because a structured payload is
    routinely nested (``{"auth": {"token": ...}}``) and a shallow pass would
    leave the credential one level down in the output.
    """
    return {key: _redact_value(key, value) for key, value in mapping.items()}


def _redact_value(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        # The whole value goes, whatever its shape. A list under ``tokens`` is a
        # list of credentials, and replacing each element one at a time would
        # keep the count and the ordering — two facts about a secret set that
        # need not survive into a log line.
        return REDACTED
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    return value


__all__ = ["REDACTED", "is_sensitive_key", "key_words", "redact"]

# ADR-012: Context Assembly Precedes Outbound Judgment

## Status

Accepted

## Context

ADR-011 establishes a unified authorization model and NEX-027 added a
provider-neutral decisioning port: an advisory service may contribute a judgment
(classification, routing, risk score, recommendation) but can never grant
authority.

That slice deliberately sent only a SHA-256 of the caller's input context rather
than the raw content, so no note text reaches a third-party judgment service and
there is nothing to retain or inject into. The digest is a genuine improvement,
but it left a gap that reads as a security property while not being one:

A SHA-256 over *the raw context* is a **fingerprint** of that context. For
low-entropy content — an email address, a note title, a two-word subject line —
an observer holding the digest can confirm a guessed value by hashing it. Sending
the digest of a secret is therefore still sending information about the secret.
"Only a hash leaves the process" is not a declassification boundary.

If context assembly is left to each caller, every future call site re-decides what
to hash, and the safe answer drifts.

## Decision

Introduce one owned domain module, `domain/agent/context.py`, that decides what
may enter an outbound judgment. Context is gathered into a `ContextEnvelope` of
named `ContextField` values, each labelled with a `DataClassification`:

| Classification | Meaning                                           |
| -------------- | ------------------------------------------------- |
| `PUBLIC`       | Safely shareable.                                 |
| `INTERNAL`     | Organisational, not for third parties (default).  |
| `CONFIDENTIAL` | The data subject's material.                      |
| `RESTRICTED`   | Must never leave the process.                     |

The envelope's `context_hash` is computed over **only** fields at or below the
envelope ceiling. Fields above it are absent from the canonical form entirely, so
their values cannot influence, and cannot be confirmed from, the digest. The
default ceiling is `INTERNAL`: internal references may inform a judgment, the
data subject's confidential material may not.

Three constraints make this structural rather than advisory:

1. **`RESTRICTED` cannot be a ceiling.** Attempting it is a construction error,
   so no configuration and no data path can select it at run time.
2. **Exclusion is recorded, never silent.** `excluded_field_names` names what was
   withheld (names, never values), and this is carried through `DecisionRequest`
   and `DecisionEvaluation` into the audit record. A silent drop would make "we
   asked about less than we held" indistinguishable from "there was nothing
   more," which is precisely the failure being prevented.
3. **Fields are named and bounded.** Names are stable lowercase identifiers so
   telemetry can aggregate them; values are capped at 200 characters and fields
   at 32 per envelope, so an unbounded document cannot be smuggled in.

`DecisionRequest.from_context` is the only sanctioned way to turn context into a
question, because it is what guarantees the hash the provider sees was computed
over content an envelope approved.

## Consequences

- A confidential value provably cannot influence an outbound digest. This is
  asserted directly: two envelopes differing only in a confidential field must
  hash identically (`tests/agent/test_context_envelope.py`).
- Labelling a value more strictly does not change its digest; the classification
  is not part of the content identity. Widening the ceiling *does* change it,
  because more fields enter the canonical form.
- Stewardship cost: a caller must now classify its inputs. That is the point —
  classification is a decision that belongs to the caller who holds the data, and
  the record makes that decision auditable.
- Callers can still hand-assemble a `context_hash` string and bypass this. The
  type system does not forbid it. Documented in the `from_context` docstring as
  taking on the declassification decision personally; a future slice may narrow
  `context_hash` to a type only an envelope can construct.
- The judgment path remains unable to reach authorization; an architecture test
  pins that these modules never import the authorization provider.

## References

- `docs/ADR/ADR-011-unified-authorization-model.md`
- `src/notes_service/domain/agent/context.py`
- `src/notes_service/domain/agent/decisioning.py`
- `tests/agent/test_context_envelope.py`

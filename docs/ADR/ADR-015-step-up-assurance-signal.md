# ADR-015: `step-up` Is Satisfied by a Recorded Authentication Assurance, Not a Judgement

## Status

Proposed

## Context

ADR-014 made obligations a separate axis on `PolicyDecision` and closed the vocabulary to six
names (`domain/governance/policy.py` → `PolicyObligation`). Four now have a deterministic
mechanism at the tool gateway (`application/use_cases/tools.py`):

| Obligation | Mechanism | Slice |
| --- | --- | --- |
| `approval` | `ApprovalRequest` + human decision, for a capability the domain already treats as sensitive | pre-existing |
| `redaction` | shape-based output filter (`domain.redaction`) | NEX-054 |
| `enhanced-audit` | the record *is* the mechanism: `DecisionEvidence` sealed into the intent hash | NEX-055 |
| `rate-limit` | the `RateLimiter` port, keyed per agent and per tool | NEX-056 |

Two do not: `step-up` and `second-approver`. The gateway refuses any call a policy places them
on. That is fail-closed and honest, but it makes the obligation **inexpressible**: a policy
author who writes `step-up` gets a rule that denies everything, which is not what they meant.

`step-up` is the harder of the two, because the other four either fire or do not — they are
predicates over facts the gateway already holds. `step-up` asks a *judgement*: "is this actor's
current assurance enough for this action?" A judgement made by a language model would be
exactly what §10 of the operating directive forbids — a model raising its own requirement. So
the open question is not *what should decide*, but **what deterministic, already-recorded fact
can stand in for the judgement**.

## Decision

### 1. The signal is the recorded authentication assurance of the delegating human

An agent has no assurance of its own. Its authority is a `DelegatedAuthorization` granted by a
human, and every tool handler acts as that human (`_actor_principal` in
`application/use_cases/tools.py` returns the *delegator*). The honest question is therefore not
"how strongly did the agent authenticate" — it presented a bearer token — but **"how strongly
did the human authenticate when they granted this authority"**.

That is a fact, recorded at consent time, and it is the only fact in the model that answers the
question. The gateway reads it; it does not infer it.

### 2. Assurance is a closed, ordered vocabulary; absent means lowest

```python
class AuthenticationAssurance(StrEnum):
    SINGLE_FACTOR = "single-factor"  # rank 1
    MULTI_FACTOR = "multi-factor"  # rank 2
```

Ordering, not equality, because a floor is a minimum and a future third level must not require
editing every call site. It is a closed vocabulary for the same reason `PolicyObligation` is:
a free-text assurance label in an enforcement path is a suggestion, and two deployments spelling
"mfa", "mfa2" and "multi-factor" differently would each silently mean whatever they liked.

**Absent or unrecognised reads as the lowest level.** A delegation written before this column
existed, or an IdP that returns no `acr`/`amr`, is *not* evidence of strong authentication.
Reading NULL as "strong enough" is how an unpersisted column becomes permanent authority — the
same fail-closed reasoning `ApprovalRequest.is_expired` already applies to a missing
`expires_at`.

### 3. Assurance is derived server-side, never accepted from a request

It is read from the IdP's `acr` / `amr` claims during the OIDC code exchange
(`infrastructure/identity/oidc_relying_party.py`) and carried on the `Principal` as an
`assurance` field. `DelegatedAuthorization.grant` copies it from the granting principal. No
route, header, or body ever supplies it, for the same reason no route supplies `owner_id`: an
actor that can assert its own assurance level has no assurance level.

### 4. Satisfaction is a static comparison against a static floor

```python
STEP_UP_FLOOR = AuthenticationAssurance.MULTI_FACTOR

satisfied = delegation.assurance.rank >= STEP_UP_FLOOR.rank
```

The floor is a module constant, not a policy parameter. `PolicyObligation` carries no
parameters (see the `_enforce_rate_limit_obligation` docstring: a parameterised obligation is a
vocabulary change and therefore an ADR). A policy says "this action requires a stepped-up
actor"; the platform says what "stepped up" means. That split is the same one `rate-limit` uses
— the rule names the requirement, the deployment sets the number — and it keeps the meaning of
`step-up` from drifting per policy version.

### 5. What "stepped up" means: the human re-authenticated *before* granting

A step-up is an interactive re-authentication (an OIDC prompt with `acr_values` demanding
multi-factor). It cannot happen *inside* a tool-gateway call: the agent holds a bearer token and
has no browser, so there is nothing to challenge. The design is therefore:

- the human steps up during login, or re-authenticates to obtain a fresh short-TTL delegation;
- the resulting assurance is recorded on the delegation;
- the gateway checks the recorded level on every call.

A deployment that needs a *per-call* freshness bound expresses it with a short delegation TTL,
which the model already supports, rather than by inventing a mid-call challenge that no caller
could answer.

### 6. The consequence for the code today

The gateway continues to refuse `step-up`, and must keep doing so, because **no assurance is
recorded yet**. The prerequisite is the vocabulary and its persistence (NEX-058). Until that
lands, refusing is the correct behaviour: an obligation "satisfied" by a weaker check than its
name promises is worse than a refusal, because the policy would read as enforced.

## Alternatives considered and rejected

**A model-scored risk signal.** Forbidden by §10: a model may recommend, never raise its own
requirement. It is also unauditable — "the model judged the risk high" is not a fact an auditor
can re-derive.

**Accumulated risk in the window (a count of recent denials).** Tempting because the data
exists, and rejected because it measures *behaviour*, not *identity*, and it is
attacker-controllable in both directions: an attacker who stays quiet is never stepped up, and
an attacker can drive up denials for a victim's agent to force step-up prompts on them. A
control an adversary can steer is not a control.

**The capability's declared sensitivity alone.** `ToolDefinition.requires_approval` already
encodes that, and `approval` already enforces it. Deriving `step-up` from it would make the two
obligations the same obligation under two names — a vocabulary that lies about its own size.

**The classification of the data the call would touch.** Classification governs *what data may
flow*, which `redaction` and the context rules already enforce. It says nothing about *who is
acting*, which is the question `step-up` asks. Using it would also let a policy author widen the
set of actions requiring step-up by relabelling data, which is the wrong lever.

**Comparing the delegator's *current session* assurance at call time.** Closer to correct, and
rejected for two reasons: the delegation may outlive the session that created it, so there is no
"current session" to read; and reading a live session per tool call couples the gateway to the
session store for a fact that should have been fixed at consent. Recording assurance on the
delegation makes the authority and its strength travel together.

## Consequences

**Positive.** `step-up` becomes expressible and auditable. The decision is reproducible from
stored facts. The same vocabulary unblocks `second-approver`, which needs to count *distinct*
humans — a notion that also requires knowing who authenticated how.

**Negative / accepted.** A delegation records the assurance at grant time, so a human who later
weakens their login method does not retroactively weaken an existing delegation; revocation and
TTL are the mechanisms for that, and they already exist. This is a deliberate trade: the
alternative is re-reading identity state on every tool call.

**Risk.** The `acr`/`amr` mapping is IdP-specific. A provider that returns no assurance claims
yields `single-factor`, so `step-up` policies refuse — the safe direction, but it will look like
a broken deployment to an operator whose IdP does support MFA. The adapter must therefore log
the raw claim shape it saw (not the value) when it defaults to the lowest level.

## Prerequisites for implementation (NEX-058)

1. `domain/identity/assurance.py` — the enum, `rank`, `meets()`.
2. `Principal.assurance`, defaulting to `SINGLE_FACTOR`.
3. `DelegatedAuthorization.assurance`, set in `grant()` from the principal.
4. `DelegationRow.assurance` + migration (NOT NULL, server default `single-factor`, then drop
   the default — the `b7d2c48e91af` pattern).
5. `_delegation_to_domain` / `add` / `save` carry the field.
6. Gateway: `STEP_UP` satisfiable iff `resolved.delegation.assurance.meets(STEP_UP_FLOOR)`.
7. Tests: raise path (single-factor delegation refused), satisfy path (multi-factor allowed),
   fail-closed path (unknown/absent assurance refused).

## References

- `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md` — session security: "step-up authentication and
  reauthentication for sensitive administration, privilege escalation, break-glass,
  investigations, exports, and critical cross-tenant actions"; actor context includes
  "authentication context / assurance".
- ADR-011 item 5 (required obligations enforced before execution).
- ADR-014 (obligations are a separate axis).
- `docs/NEXUS/BACKLOG.md` — NEX-057, NEX-058.

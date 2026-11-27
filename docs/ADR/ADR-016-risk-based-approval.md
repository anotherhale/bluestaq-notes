# ADR-016: Risk Is Derived From the Capability, and It Can Only Raise the Gate

## Status

Accepted

## Context

The security architecture asks for two things the platform did not have
(`docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`):

- L212 — Tier 2 risk-based approval is **durable** and covers "bulk operations, elevated
  access, unusual patterns, exports, suspicious activity".
- L147 — broad-impact or lockout-prone changes "require **risk-based approval** and
  preview", and mandatory platform constraints cannot be weakened by tenant config.

`PolicyDecision` already carried an authorization verdict, obligations, and (since
NEX-058) authentication assurance. It carried no notion of *how dangerous the action is*.
An audit reader could see that `delete_note` was allowed and that `read_note` was allowed,
with nothing on the record to distinguish them.

Approval already existed: `ApprovalRequest.required_approvals` (closed vocabulary 1 | 2) plus
`second_approver_user_id` (NEX-059). But the threshold was **only** what the policy author
wrote. A policy that said nothing produced one signature for every capability alike, so a
lockout-prone action and a routine read shared the same human gate. "Risk-based" was
therefore a name without a mechanism.

The open question is not *whether* to consider risk. It is **who decides it**. §10 of the
operating directive says a model may reason, recommend, plan, classify and evaluate, but may
never widen or narrow its own authority. A risk score that sets an approval threshold *is*
authority. So the decision cannot be a model output.

## Decision

### 1. Risk is a pure function of the capability

`domain/agent/risk.py` maps `Capability` → a closed, ordered `RiskTier`:

| Tier | Capabilities | Why |
| --- | --- | --- |
| `HIGH` | `ORG_SEARCH` | the only capability whose blast radius reaches past the acting owner's own data, across tenants at once |
| `ELEVATED` | `DELETE_NOTE`, `SHARE_NOTE`, `REVOKE_SHARE` | destructive or visibility-changing, but bounded to data the owner already controls |
| `LOW` | reads, creates, appends, listings | touches no existing data |

The function is total, deterministic and I/O-free, and raises `TypeError` on a non-`Capability`
rather than coercing a string. A reviewer reproduces any tier by reading the file, which is
what makes a recorded risk *explainable* rather than merely plausible.

`SENSITIVE_CAPABILITIES` already marked the four that need *a* human. Risk is deliberately not
a rename of it: the tier separates those four by **what the action can destroy or expose**, and
asserts (in test) that every sensitive capability is at least `ELEVATED`. A capability that
needed a human but classed as low-risk would mean the two sets disagree about danger.

### 2. Risk can only raise the approval threshold

`required_approvals_for(policy_threshold=..., risk=...)` returns
`min(2, max(1, policy_threshold, floor))` where `floor` is 2 for `HIGH` and 1 otherwise.

Two consequences, both intended:

- A **silent policy cannot weaken a high-risk action** to one signature. The floor is code,
  not policy — the same rule `step-up` follows: the control that requests a protection must
  not be able to define it away.
- A **policy may still raise** a low-risk action to two signatures.

The result is clamped into 1 | 2 because `ApprovalRequest` models only that set. Returning 3
would describe a requirement no code path can satisfy.

### 3. Risk is on the decision record

`_policy_attributes()` emits `risk`, so a policy rule can *require* a control at or above a
tier, and `DecisionEvidence` shows why the gate looked as it did. A policy cannot lower the
tier: it is computed from the capability in the enforcement path, never read from the policy.

## Consequences

- Approval strength now tracks blast radius without any policy change. Existing policies that
  never mention risk keep their current behaviour for `LOW` and `ELEVATED` capabilities.
- Adding a `HIGH` capability in future automatically earns a two-signature floor; no policy
  edit is needed to get the stricter gate.
- Widening the tier vocabulary or the 1–2 approval vocabulary needs a new ADR. A threshold a
  caller can widen is not a control.

## Honest limit

`ORG_SEARCH` is the only `HIGH` capability **and** it is non-delegable
(`NON_DELEGABLE_CAPABILITIES`), so no agent token can reach it today and the two-signature
floor has **no production caller yet**. It is kept because it is the correct answer the day a
future ADR permits delegated org-wide search: the control exists before the capability that
needs it, rather than being bolted on afterwards. Because that branch cannot be reached end to
end, the threshold logic is tested **directly as a pure function** rather than through a path
that cannot exist. Every other tier is exercised through the real gateway.

Deferred, and why:

- **Preview** (L147 asks for approval *and* preview) is not implemented. Preview is a
  presentation concern over a proposed diff; there is no proposal surface to render yet.
- **Risk signals beyond the capability** — bulk volume, unusual patterns, export size (L212) —
  need request-level facts the gateway does not collect. The capability tier is the part that
  can be derived from attested facts today; adding heuristics without them would be guessing
  inside an enforcement path.

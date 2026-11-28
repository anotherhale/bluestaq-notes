# ADR-014: Obligations Are a Separate Effect Axis, Not a Third Decision

## Status

Proposed

## Context

The security architecture names three policy effects — deny, allow, obligations — and
requires that obligations "must be satisfied before the operation proceeds"
(`docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`, *Semantics*). ADR-011 item 5 says the same.

The implementation models two. `PolicyEffect` has `ALLOW` and `DENY`, and its docstring
argues deliberately against a third value:

> Only two effects exist. There is deliberately no `ABSTAIN`: a policy that has nothing to
> say is represented by *not matching*, and an explicit third outcome would let a caller
> mistake "no opinion" for a decision.

That reasoning is sound and this ADR does not overturn it. The question is where
obligations live if not in `PolicyEffect`.

Today `ToolGateway` enforces policy like this (`application/use_cases/tools.py`):

```python
if decision.effect is PolicyEffect.DENY and decision.matched_rules:
    self._record_policy_denial(...)
    raise AccessDenied(...)
return consulted
```

An `ALLOW` falls through and the tool runs. There is no mechanism by which a policy can say
"allow this, but only after a second approver signs it". A policy author who needs that
today has one lever — deny — which refuses a call that should be permitted with conditions.
That is not a conservative approximation of obligations; it is a different policy.

The gap is therefore not cosmetic. It also fails closed in the wrong direction: a policy
that means "allow with redaction" currently either denies everything or allows everything,
and neither is what was authored.

## Decision

Obligations are a **separate axis on the decision**, not a third value of `PolicyEffect`.

`PolicyEffect` keeps exactly two members. `PolicyDecision` gains an ordered, immutable
tuple of obligations:

```python
@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: PolicyEffect
    matched_rules: tuple[str, ...] = field(default_factory=tuple)
    policy_versions: tuple[str, ...] = field(default_factory=tuple)
    obligations: tuple[Obligation, ...] = field(default_factory=tuple)
```

A `PolicyRule` gains an optional obligation requirement alongside its effect, so a rule can
say "allow, requiring approval". Obligations attach only to `ALLOW`: an obligation on a
`DENY` is a contradiction — the call is refused, so there is nothing to satisfy — and
constructing one raises.

### The invariant that makes this safe

**An obligation the gateway cannot satisfy denies the call.**

This is the fail-closed rule the architecture requires ("unknown ... obligations ... fail
closed"), and it is what lets the type be introduced before every obligation kind is
implemented. A `PolicyDecision` carrying an obligation the enforcement point does not
recognise must not proceed on the reasoning that "the allow matched, so it is allowed".
The allow matched *conditionally*, and an unmet condition is not a grant.

Concretely, the gateway satisfies obligations through a registry of handlers keyed by
obligation kind. A kind with no registered handler, or whose handler reports "not
satisfied", raises `AccessDenied` and records the denial — the same audit path a policy
denial uses, with the obligation named. Absence of a handler is a deny, not a skip.

### Why not a third `PolicyEffect`

Because it would collapse two independent questions into one value. Effect answers *may
this proceed at all*. Obligations answer *what must happen for it to proceed*. A call can
be allowed with zero obligations, allowed with two, or denied — three states on one axis
and a set on the other. Encoding the set as an enum member forces a caller to switch on a
value that then has to carry a payload anyway, and loses the "allowed *and* conditional"
case that is the entire point.

It would also put obligation semantics inside `evaluate()`, which is a pure function over
an attribute snapshot. `evaluate()` cannot know whether an approval was obtained, so it
could at best emit "needs approval" — a token that the gateway must then interpret. Emitting
a typed obligation list is the same information without pretending evaluation performed
the check.

### Where satisfaction happens

At the enforcement point, not in evaluation. `evaluate()` is pure and decides; the gateway
holds the side effects and the audit trail. This preserves the existing boundary: the domain
never imports infrastructure, and the architecture test in
`tests/architecture/test_layering.py` stays green.

Satisfaction must also be revalidated against the clock that will execute the action. An
approval obtained at T that expires at T+5 cannot authorise a call at T+10 — the same
staleness rule already applied to approval decisions (NEX-026). An obligation carries the
evidence it was satisfied, and the gateway checks that evidence at execution time.

## Consequences

- `PolicyEffect` is untouched, so the existing deny-wins ordering and default-deny rules
  are unchanged. No current test needs to change meaning.
- `PolicyDecision` grows one field with a default, so existing construction sites keep
  working.
- The gateway gains an obligation registry and a new denial path. This is the security-
  sensitive part and needs its own tests: unknown obligation denies, unmet obligation
  denies, satisfied obligation proceeds and is audited.
- Audit records gain the obligation list and its satisfaction outcome. An allow that
  required an approval must be distinguishable in the audit trail from an unconditional
  allow.
- Until a handler is registered for a kind, that kind is unusable — which is the correct
  default and means the feature can land incrementally without a window in which it fails
  open.

## Alternatives considered

**A third `PolicyEffect.OBLIGATION`.** Rejected above: collapses two axes, and leaves
"allowed with conditions" unrepresentable.

**Encode obligations as ordinary deny rules plus a side channel.** Rejected: the side
channel becomes authoritative and is not part of the decision, so it cannot be audited as
part of the decision and can drift from it.

**Require all obligation kinds be implemented before the type lands.** Rejected: it makes
the slice enormous, and the fail-closed default already makes a partial implementation
safe. Unknown obligation denies.

**Evaluate obligations inside `evaluate()`.** Rejected: `evaluate()` is pure and cannot
observe approvals, step-up tokens, or the clock. It would have to trust a caller-supplied
"obligations satisfied" flag, which is exactly the kind of unverified claim the gateway
exists to refuse.

## References

- `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md` — *Semantics*, *Decision evidence*,
  *Authorization Component Boundaries*
- `docs/ADR/ADR-011-unified-authorization-model.md` — item 5, required obligations
- `docs/ADR/ADR-012-context-assembly-before-judgment.md` — evidence precedes judgment
- `src/notes_service/domain/governance/policy.py` — `PolicyEffect`, `PolicyRule`,
  `PolicyDecision`, `evaluate()`
- `src/notes_service/application/use_cases/tools.py` — `ToolGateway` enforcement

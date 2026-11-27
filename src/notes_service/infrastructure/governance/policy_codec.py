"""JSON codec for the policy condition AST (NEX-048).

A stored policy is *untrusted input on the way back in*. Two things follow, and
this module exists to make both true in one place:

* **Decoding re-validates through the domain.** Every node is rebuilt by calling
  its constructor, so ``Comparison``, ``AllOf``, ``AnyOf`` and ``Not`` run their
  own validators and ``_check_bounds`` runs again. A row written by older code
  therefore cannot smuggle in a node type, an operator, or a value combination the
  current validator refuses — the domain is the only authority on what a policy
  is, and the codec never becomes a second one.
* **Encoding is total and lossless.** Every field the domain compares is written,
  including the attribute's declared type (which travels with the reference) and
  the node kind. A round trip that dropped ``AttributeType`` would turn a
  ``NUMBER`` comparison into a ``STRING`` one on the next read and change how a
  stored policy evaluates.

The wire shape is deliberately boring and self-describing — a ``kind`` tag per
node and no polymorphism on value type — because it must stay readable in a row
during an incident, and because an unexpected key should be a validation error
rather than a silently ignored field.

Why not pickle or a library encoder
-----------------------------------

``pickle`` is arbitrary code execution on read: a policy table is exactly the
wrong place for it. A generic object encoder would need the domain classes to
carry serialisation concerns, which the domain deliberately does not. An explicit
codec keeps the dependency direction pointing inward — infrastructure knows the
domain, never the reverse — and keeps the closed node set closed: an unknown
``kind`` raises instead of being decoded into something new.
"""

from __future__ import annotations

import json
from typing import Any

from notes_service.domain.errors import ValidationError
from notes_service.domain.governance.policy import (
    AllOf,
    AnyOf,
    AttributeRef,
    AttributeType,
    Comparison,
    Condition,
    Not,
    PolicyEffect,
    PolicyRule,
)

# Operator groups the domain defines, mirrored here only to decide whether a
# decoded value list becomes a tuple. Membership is still validated by
# ``Comparison``; this set is not a second source of truth for what is legal.
_SET_OPERATORS = frozenset({"in", "not_in"})


def encode_condition(condition: Condition) -> dict[str, Any]:
    """Serialise one condition node to a JSON-ready mapping."""
    if isinstance(condition, Comparison):
        return {
            "kind": "comparison",
            "attribute": {"name": condition.attribute.name, "type": condition.attribute.type.value},
            "operator": condition.operator,
            "value": _encode_value(condition.value),
        }
    if isinstance(condition, AllOf):
        return {"kind": "all", "children": [encode_condition(c) for c in condition.children]}
    if isinstance(condition, AnyOf):
        return {"kind": "any", "children": [encode_condition(c) for c in condition.children]}
    if isinstance(condition, Not):
        return {"kind": "not", "child": encode_condition(condition.child)}
    # Unreachable while the node set is closed, but an encoder that silently
    # drops an unknown node would store a policy that is not the one in memory.
    raise ValidationError(f"cannot encode unknown condition node: {type(condition).__name__}")


def _encode_value(value: object) -> object:
    if isinstance(value, tuple | frozenset):
        return list(value)
    return value


def decode_condition(payload: object) -> Condition:
    """Rebuild a condition node, re-running every domain validator."""
    if not isinstance(payload, dict):
        raise ValidationError("a serialised condition must be an object")
    kind = payload.get("kind")
    if kind == "comparison":
        return _decode_comparison(payload)
    if kind in {"all", "any"}:
        children = payload.get("children")
        if not isinstance(children, list):
            raise ValidationError(f"{kind} node requires a children list")
        decoded = tuple(decode_condition(child) for child in children)
        return AllOf(decoded) if kind == "all" else AnyOf(decoded)
    if kind == "not":
        return Not(decode_condition(payload.get("child")))
    raise ValidationError(f"unknown condition node kind: {kind!r}")


def _decode_comparison(payload: dict[str, Any]) -> Comparison:
    attribute = payload.get("attribute")
    if not isinstance(attribute, dict):
        raise ValidationError("a comparison requires an attribute object")
    raw_type = attribute.get("type")
    if not isinstance(raw_type, str):
        raise ValidationError(f"an attribute type must be a string, not {raw_type!r}")
    try:
        attr_type = AttributeType(raw_type)
    except ValueError as error:
        raise ValidationError(f"unknown attribute type: {raw_type!r}") from error
    ref = AttributeRef(name=attribute.get("name"), type=attr_type)  # type: ignore[arg-type]
    operator = payload.get("operator")
    raw_value = payload.get("value")
    value: object = (
        tuple(raw_value)
        if operator in _SET_OPERATORS and isinstance(raw_value, list)
        else raw_value
    )
    return Comparison(attribute=ref, operator=operator, value=value)  # type: ignore[arg-type]


def encode_rules(rules: tuple[PolicyRule, ...]) -> str:
    """Serialise a policy's rules to a JSON string for storage."""
    return json.dumps(
        [
            {
                "rule_id": rule.rule_id,
                "effect": rule.effect.value,
                "when": encode_condition(rule.when) if rule.when is not None else None,
            }
            for rule in rules
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_rules(payload: str) -> tuple[PolicyRule, ...]:
    """Rebuild a policy's rules from its stored JSON, re-validating each rule."""
    try:
        raw = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"stored policy rules are not valid JSON: {error}") from error
    if not isinstance(raw, list):
        raise ValidationError("stored policy rules must be a JSON array")
    rules: list[PolicyRule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValidationError("a stored rule must be an object")
        when = entry.get("when")
        effect = entry.get("effect")
        rules.append(
            PolicyRule(
                rule_id=entry.get("rule_id"),  # type: ignore[arg-type]
                effect=PolicyEffect(effect),  # type: ignore[arg-type]
                when=decode_condition(when) if when is not None else None,
            )
        )
    return tuple(rules)


__all__ = [
    "decode_condition",
    "decode_rules",
    "encode_condition",
    "encode_rules",
]

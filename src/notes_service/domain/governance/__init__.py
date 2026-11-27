"""The governance bounded context.

Public surface for governance policy. Importing from here keeps call sites stable
if the internal module layout changes.
"""

from __future__ import annotations

from notes_service.domain.governance.policy import (
    MAX_CONDITION_DEPTH,
    MAX_CONDITION_NODES,
    MAX_POLICY_ID_LENGTH,
    MAX_REASON_LENGTH,
    AllOf,
    AnyOf,
    AttributeRef,
    AttributeType,
    Comparison,
    Condition,
    Not,
    PolicyDecision,
    PolicyEffect,
    PolicyObligation,
    PolicyRule,
    PolicyStatus,
    PolicyVersion,
    evaluate,
    new_policy_version,
    new_policy_version_id,
)

__all__ = [
    "MAX_CONDITION_DEPTH",
    "MAX_CONDITION_NODES",
    "MAX_POLICY_ID_LENGTH",
    "MAX_REASON_LENGTH",
    "AllOf",
    "AnyOf",
    "AttributeRef",
    "AttributeType",
    "Comparison",
    "Condition",
    "Not",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyObligation",
    "PolicyRule",
    "PolicyStatus",
    "PolicyVersion",
    "evaluate",
    "new_policy_version",
    "new_policy_version_id",
]

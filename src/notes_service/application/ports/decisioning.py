"""The decision provider port.

A decision provider is a *judgment* service: it is asked a bounded question and
returns a bounded, advisory answer. It is deliberately not an LLM chat port. The
difference matters:

* a chat port hands the model an open instruction and returns free-form text the
  caller must interpret, which is exactly the shape that turns a model into an
  authority;
* this port hands over a typed question with enumerated candidates and returns a
  typed answer the application validates before use.

The port is one method wide so an adapter cannot grow an escape hatch, and it
must not raise for a business condition: a provider that cannot answer returns an
abstention. The use case still guards against a raised exception, because a
third-party client can always fail in a way its adapter did not anticipate.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notes_service.domain.agent.decisioning import Decision, DecisionRequest


@runtime_checkable
class DecisionProvider(Protocol):
    """Returns structured judgments. Never grants authority."""

    @property
    def name(self) -> str:
        """Stable provider identifier recorded in telemetry, e.g. ``stub``."""
        ...

    def decide(self, request: DecisionRequest) -> Decision: ...

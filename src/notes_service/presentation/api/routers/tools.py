"""Tool gateway endpoints under ``/api/v1/agents/tools``.

This is the HTTP surface of the governed gateway. It is deliberately the
thinnest possible layer: it authenticates the *agent* (not a human), maps the
request body to :class:`InvokeTool`, and renders the audited outcome. Every
security decision — audience, revocation, capability, approval, audit — is made
in the application layer, so this router cannot be a second, weaker path.

Two properties are load-bearing and pinned by tests:

* **A human cannot call it.** Authentication reads only the ``Authorization``
  bearer header and never the demo-identity header, so possessing a demo user id
  is not authority to invoke a tool.
* **Audience is enforced, not assumed.** The gateway resolves the token for
  ``TokenAudience.TOOL_GATEWAY``, so a Notes-API token presented here is refused
  even though it is otherwise valid.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from notes_service.presentation.dependencies import (
    AgentRateLimitDep,
    AgentTokenSecretDep,
    ContainerDep,
)
from notes_service.presentation.schemas import (
    ToolInvocationRequest,
    ToolInvocationResponse,
)

router = APIRouter(prefix="/agents/tools", tags=["tools"])


@router.post(
    "/invoke",
    response_model=ToolInvocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Invoke one governed tool with an agent token",
    dependencies=[AgentRateLimitDep],
)
def invoke_tool(
    payload: ToolInvocationRequest,
    secret: AgentTokenSecretDep,
    container: ContainerDep,
) -> ToolInvocationResponse:
    result = container.invoke_tool().run(
        secret=secret,
        tool_name=payload.tool,
        arguments=payload.arguments,
        idempotency_key=payload.idempotency_key,
    )
    return ToolInvocationResponse.from_result(result)

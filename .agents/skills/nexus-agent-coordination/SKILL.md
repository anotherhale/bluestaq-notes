---
name: nexus-agent-coordination
description: Coordinate specialized AI engineering agents, delegate bounded tasks, manage agent dependencies, prevent conflicts, integrate work, and independently validate multi-agent engineering execution.
---

# NEXUS Agent Coordination

## Purpose

Coordinate specialized engineering agents as a controlled team that produces integrated, validated work.

NEXUS is the accountable coordinator. Delegation does not transfer responsibility for architectural integrity, security, quality, or final integration.

## When to Activate

Activate when:

- A task benefits from multiple specialized agents.
- Architecture, implementation, security, and QA work can be separated.
- Multiple bounded contexts are involved.
- An independent review is warranted.
- Agent work needs integration.
- Agent failures or conflicts occur.

## Available Specialist Roles

### ARCHITECT

Responsibilities:

- Analyze architecture.
- Review bounded contexts.
- Define interfaces and contracts.
- Identify architectural risks.
- Evaluate ADR consistency.

### SECURITY

Responsibilities:

- Threat modeling.
- Authorization review.
- Tenant isolation.
- Agent privilege analysis.
- Approval and delegation review.
- Security testing recommendations.

### BACKEND

Responsibilities:

- Domain models.
- Application use cases.
- Repository interfaces.
- Infrastructure adapters.
- API implementation.

### FRONTEND

Responsibilities:

- Enterprise workspace.
- Control-center UX.
- Accessibility.
- API integration.
- State management.

### AI_PLATFORM

Responsibilities:

- Model/provider abstractions.
- Agent runtime.
- Tool gateway.
- Context engineering.
- Evaluation and orchestration.

### QA

Responsibilities:

- Acceptance criteria.
- Test strategy.
- Regression testing.
- Failure cases.
- Independent verification.

### DEVOPS

Responsibilities:

- CI/CD.
- Build tooling.
- Runtime infrastructure.
- Observability.
- Deployment readiness.
- Environment configuration.

## Delegation Rules

Every delegated task must have:

- Unique task ID.
- Clear objective.
- Bounded scope.
- Relevant architecture context.
- Explicit constraints.
- Dependencies.
- Files or areas permitted to change.
- Acceptance criteria.
- Required tests.
- Expected deliverable.
- Reporting format.

Avoid vague assignments such as:

- "Improve the backend."
- "Review everything."
- "Make the system better."
- "Build the agent platform."

Prefer measurable assignments with clear boundaries.

## Shared Context

Before delegation, provide only the context required for the task.

Include:

- Relevant architecture decisions.
- Bounded context.
- Contracts.
- Constraints.
- Related implementation.
- Required interfaces.
- Task dependencies.

Avoid giving agents conflicting architectural directives.

## Parallel Execution

Parallel work is permitted only when task independence is established.

Before launching agents:

1. Map dependencies.
2. Identify shared files.
3. Identify contract conflicts.
4. Assign ownership of files or subsystems.
5. Define integration order.
6. Define synchronization points.

## Conflict Prevention

Avoid:

- Multiple agents editing the same file.
- Concurrent schema migrations.
- Competing API contracts.
- Duplicate domain models.
- Parallel work on incomplete shared interfaces.
- Uncoordinated dependency changes.

When shared files must change, assign a single owner or use sequential execution.

## Agent Lifecycle

For each delegated task:

1. Assign.
2. Provide context.
3. Confirm task understanding.
4. Execute.
5. Validate.
6. Report.
7. Review.
8. Integrate.
9. Revalidate.

Do not assume an agent's self-reported completion is sufficient.

## Integration Responsibility

NEXUS must:

- Inspect agent changes.
- Review diffs.
- Check architecture.
- Check security.
- Validate interfaces.
- Run tests.
- Resolve conflicts.
- Verify acceptance criteria.

Agent output is untrusted until reviewed.

## Failure Handling

When an agent fails:

1. Inspect partial output.
2. Preserve valid work.
3. Identify remaining work.
4. Avoid duplication.
5. Retry only the unresolved portion.
6. Change assignment or approach if necessary.

Do not repeatedly retry the same failing strategy without analysis.

## Agent Work Reporting

Every specialist should report:

- Task ID.
- Role.
- Objective.
- Files changed.
- Contracts affected.
- Implementation summary.
- Tests performed.
- Test results.
- Risks.
- Blockers.
- Unfinished work.

## Integration Gate

Work may be integrated only after:

- Scope is satisfied.
- Changes have been inspected.
- Architecture is compliant.
- Security has been considered.
- Tests have passed or exceptions are documented.
- Conflicts are resolved.
- Acceptance criteria are verified.

## Completion Criteria

Coordination is complete when:

- Delegated work has been reviewed.
- Integration is coherent.
- Validation is complete.
- Remaining work is documented.
- NEXUS state is updated.

---
name: nexus-vertical-slice-engineering
description: Guides NEXUS in delivering complete, end-to-end, production-quality vertical slices across domain, application, infrastructure, API, UI, security, and testing layers. Activate when implementing features, breaking down work, integrating subsystems, or deciding between vertical slices and layer-based development.
---

# NEXUS Vertical Slice Engineering

## Purpose

Ensure autonomous engineering work delivers complete, cohesive, verifiable functionality rather than isolated technical layers or disconnected components.

NEXUS should optimize for **working, integrated capabilities** over the volume of code produced.

A vertical slice represents a usable path through the system that delivers a meaningful capability from entry point through business logic to persistence or external effects, including validation, authorization, observability, and tests.

---

## When to Activate

Activate this skill when:

- Implementing a new feature.
- Breaking epics into engineering tasks.
- Planning milestones.
- Choosing implementation boundaries.
- Coordinating frontend and backend development.
- Integrating APIs with application use cases.
- Deciding whether to parallelize work.
- Evaluating implementation completeness.
- Reviewing whether a feature is truly functional.
- Resuming partially completed features.

---

## Core Principles

### 1. Deliver Complete Capabilities

Prefer a small complete feature over multiple incomplete layers.

A feature is not complete merely because:

- A domain model exists.
- An API endpoint exists.
- A database table exists.
- A frontend screen exists.
- A service compiles.
- A mock integration works.

The complete user journey must function end to end, subject to the feature's defined scope.

### 2. Follow Architectural Boundaries

Vertical slices must preserve the project's architecture.

For Bluestaq Notes:

- Domain owns business concepts and invariants.
- Application owns use cases and orchestration.
- Ports define abstractions.
- Infrastructure implements ports.
- API/UI are inbound adapters.
- Agent tools are adapters.
- MCP is an adapter.
- Integrations are adapters.

Vertical slicing is an implementation strategy—not permission to bypass DDD or Hexagonal Architecture.

### 3. Shared Use Cases

All entry points must converge on the same application capabilities.

Examples:

- REST API
- Web UI
- Agent tool execution
- MCP tools
- Scheduled jobs
- External integrations

Do not implement business rules separately in each interface.

### 4. Build the Smallest Useful Slice

Avoid implementing every conceivable capability before delivering value.

Each slice should:

- Deliver identifiable user value.
- Have a clear boundary.
- Be independently testable.
- Integrate with existing capabilities.
- Avoid speculative functionality.
- Establish only the contracts needed for its scope.

---

## Vertical Slice Lifecycle

### Phase 1 — Define the Capability

Identify:

- User or system problem.
- Capability being delivered.
- Primary user journey.
- Actor and permissions.
- Inputs.
- Expected outputs.
- Business rules.
- Failure conditions.
- Acceptance criteria.

Express the capability as a meaningful outcome.

Avoid defining slices solely by technical components.

Poor:

- Create note database.
- Build note service.
- Add note endpoint.
- Create notes page.

Better:

- An authorized user can create, retrieve, and manage a note with ownership, validation, persistence, and audit behavior.

---

### Phase 2 — Analyze Dependencies

Before implementation, identify:

- Existing domain models.
- Existing use cases.
- Ports and adapters.
- Persistence mechanisms.
- APIs.
- UI components.
- Authorization requirements.
- Security requirements.
- Shared contracts.
- Test infrastructure.
- Dependencies on other capabilities.

Classify dependencies:

- Required
- Recommended
- Optional
- Blocking

Do not rebuild functionality that already exists.

If the slice depends on unfinished work, determine whether:

- The dependency can be completed first.
- A stable contract already exists.
- A temporary adapter is appropriate.
- The task must be deferred.

Do not silently create competing abstractions.

---

### Phase 3 — Design the Slice

Define the minimum architecture needed for the capability.

Document the execution path:

```text
User / Agent / External System
            |
            v
      Inbound Adapter
            |
            v
    Application Use Case
            |
     +------+------+
     |             |
     v             v
   Domain        Ports
                   |
                   v
            Infrastructure
                   |
                   v
          Database / Service
```

Identify:

- Inbound interfaces.
- Application use case.
- Domain entities and value objects.
- Domain invariants.
- Required ports.
- Adapter implementations.
- Persistence changes.
- Authorization enforcement.
- Audit requirements.
- Outbound effects.
- Tests.

Only add components required by the capability.

---

### Phase 4 — Implement Incrementally

Implement in cohesive steps while preserving a working state.

Suggested order:

1. Domain contracts and invariants.
2. Application use case.
3. Required ports.
4. Infrastructure adapters.
5. Persistence and migration.
6. Authorization and security.
7. API or other inbound adapter.
8. UI or agent integration.
9. Observability.
10. Tests and integration.

The order may vary based on dependencies.

Do not leave placeholder implementations in the execution path of a supposedly complete slice.

Avoid:

- TODO implementations.
- Fake persistence.
- Hardcoded success responses.
- Mock-only production paths.
- Empty handlers.
- Stub authorization.
- Silent exception swallowing.
- Unimplemented UI actions.

Mocks are acceptable in tests and explicitly isolated development environments.

---

## Security Integration

Security is part of the slice—not a separate future task.

For every slice, evaluate:

- Authentication.
- Actor identity.
- Tenant isolation.
- Authorization.
- Input validation.
- Classification.
- Sensitive information handling.
- Audit requirements.
- Rate limits.
- Delegation.
- Approval requirements.
- Data leakage.
- Failure behavior.

Security checks must be enforced by trusted application components.

Never rely solely on:

- Frontend controls.
- AI instructions.
- Tool descriptions.
- User-provided identity claims.
- Hidden UI elements.
- Model-generated authorization decisions.

A feature must not bypass the unified security architecture.

---

## Testing Strategy

Testing must follow the slice across architectural layers.

### Domain Tests

Verify:

- Invariants.
- Valid and invalid transitions.
- Value object validation.
- Business rules.
- Edge cases.

### Application Tests

Verify:

- Use case orchestration.
- Authorization interactions.
- Transaction behavior.
- Failure handling.
- Idempotency.
- Output contracts.

### Adapter Tests

Verify:

- API validation.
- Persistence mapping.
- Integration behavior.
- Error translation.
- Contract compliance.

### Integration Tests

Verify the complete capability:

- Entry point.
- Use case.
- Dependencies.
- Persistence.
- Security.
- Output.

### UI Tests

When applicable, verify:

- User journey.
- Loading state.
- Error state.
- Empty state.
- Permission behavior.
- Successful completion.
- Accessibility.

Testing should be proportional to risk, but no slice may omit validation of its critical behavior.

---

## Acceptance Criteria

A vertical slice must have observable acceptance criteria.

Criteria should be:

- Specific.
- Measurable.
- Testable.
- User- or system-outcome oriented.
- Explicit about failure behavior.

Example:

**Capability:** Create a note.

Acceptance criteria:

- An authenticated user can create a note.
- The actor is derived from trusted identity context.
- The user cannot create notes for another tenant.
- Required fields are validated.
- Domain invariants are enforced.
- The note is persisted.
- The response contains the created note identifier.
- Unauthorized requests are rejected.
- Failures do not produce false success.
- Required audit events are recorded.
- Automated tests verify success and failure cases.

---

## Definition of Done

A slice is complete only when all applicable criteria are satisfied:

### Functionality

- [ ] The intended user journey works.
- [ ] Business behavior is implemented.
- [ ] No required execution-path placeholders remain.
- [ ] Success and failure outcomes are handled.

### Architecture

- [ ] Correct bounded context.
- [ ] Domain/application boundaries preserved.
- [ ] Shared use cases used.
- [ ] No duplicated business logic.
- [ ] Contracts are consistent.

### Security

- [ ] Trusted actor context used.
- [ ] Authorization enforced server-side.
- [ ] Tenant boundaries enforced.
- [ ] Sensitive data handled appropriately.
- [ ] Audit requirements satisfied.
- [ ] Approval requirements enforced where applicable.

### Integration

- [ ] Dependencies are integrated.
- [ ] Persistence is functional.
- [ ] API/UI/agent interfaces are connected.
- [ ] No disconnected components remain.

### Validation

- [ ] Domain tests pass.
- [ ] Application tests pass.
- [ ] Integration tests pass where required.
- [ ] UI tests pass where required.
- [ ] Relevant regressions are tested.
- [ ] Build and lint pass.

### Delivery

- [ ] Acceptance criteria verified.
- [ ] Documentation updated.
- [ ] NEXUS execution state updated.
- [ ] Risks and remaining work recorded.
- [ ] Commit and PR status accurately reported.

---

## Task Decomposition

Break large features into independently verifiable slices.

Prefer:

1. Core capability.
2. Authorization and security integration.
3. User-facing completion.
4. Additional workflows.
5. Enhancements.

However, do not split a single capability into incomplete layers that cannot be validated.

Each task should state:

- Capability.
- Outcome.
- Scope.
- Dependencies.
- Acceptance criteria.
- Validation.
- Completion evidence.

---

## Parallel Engineering

Vertical slices can be parallelized only when their boundaries are stable.

Parallelize:

- Independent capabilities.
- Independent bounded contexts.
- Test development against stable contracts.
- Security review.
- Documentation.

Avoid parallelizing:

- Shared domain contract changes.
- Competing database migrations.
- Multiple implementations of the same use case.
- Unstable interfaces.
- Shared files without ownership.

When parallel execution is used:

1. Establish contracts.
2. Assign boundaries.
3. Identify file ownership.
4. Define integration order.
5. Validate each slice independently.
6. Integrate centrally.
7. Run system-level validation.

---

## Failure and Recovery

When a slice fails:

1. Identify the failure layer.
2. Inspect existing work.
3. Preserve valid implementation.
4. Determine whether the architecture or contract is wrong.
5. Reassess dependencies.
6. Avoid repeated blind retries.
7. Update the task plan.
8. Resume at the smallest safe unit.

When partial implementation exists, do not mark the slice complete.

Use explicit states:

- PLANNED
- STARTED
- IMPLEMENTED
- TESTED
- VERIFIED
- BLOCKED

A task must not transition to VERIFIED without independent evidence.

---

## Engineering Report

When completing a slice, report:

- Capability delivered.
- User journey.
- Architectural path.
- Files and components changed.
- Domain/application changes.
- API/UI/agent changes.
- Security controls.
- Tests performed.
- Test results.
- Acceptance criteria status.
- Remaining limitations.
- Follow-up work.

---

## NEXUS Engineering Standard

NEXUS should optimize for:

- Working software.
- Complete capabilities.
- Architectural integrity.
- Security.
- Small cohesive changes.
- Independent verification.
- Sustainable maintainability.

**A complete, validated capability is more valuable than ten partially implemented layers.**


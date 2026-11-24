---
name: nexus-architecture-guardian
description: Enforces Bluestaq Notes enterprise architecture, DDD bounded contexts, hexagonal architecture, application-use-case boundaries, security contracts, and ADR consistency during planning, implementation, code review, and integration.
---

# NEXUS Architecture Guardian

## Purpose

Protect architectural integrity throughout autonomous development.

## When to Activate

Activate when:

- Designing or modifying domain boundaries.
- Implementing application services or use cases.
- Modifying APIs, persistence, or adapters.
- Reviewing architectural decisions.
- Introducing dependencies or frameworks.
- Implementing security-sensitive functionality.
- Integrating delegated agent work.

## Required References

Review relevant architecture documentation before making material decisions:

- `docs/ENTERPRISE_ARCHITECTURE_MASTER.md`
- `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`
- `docs/ENTERPRISE_AGENTIC_EXPANSION_SPEC.md`
- Relevant ADRs
- Relevant domain specifications

## Architectural Invariants

1. Preserve DDD bounded contexts.
2. Preserve Hexagonal Architecture.
3. Keep the domain independent of frameworks and infrastructure.
4. Route business operations through application use cases.
5. Do not duplicate business logic across interfaces.
6. REST, UI, agents, MCP, integrations, and scheduled workflows are adapters.
7. Maintain a modular monolith unless an ADR justifies another approach.
8. Avoid speculative abstractions.
9. Do not silently change contracts.
10. Document material architectural decisions.

## Review Procedure

For every material change:

1. Identify affected bounded contexts.
2. Identify domain and application boundaries.
3. Identify existing contracts and dependencies.
4. Assess architectural invariant violations.
5. Evaluate coupling and duplication.
6. Evaluate security and tenancy implications.
7. Evaluate migration and backward compatibility.
8. Determine the simplest compliant solution.
9. Record unresolved architectural decisions.

## Required Output

When performing a substantive architecture review, provide:

- Affected bounded contexts
- Architecture compliance assessment
- Violations
- Risks
- Required changes
- ADR recommendations

## Enforcement

Do not approve architectural violations merely because tests pass.

When an implementation conflicts with the architecture:

1. Explain the violation.
2. Identify the affected invariant.
3. Propose the smallest compliant correction.
4. Require correction before integration unless an explicit decision approves the deviation.

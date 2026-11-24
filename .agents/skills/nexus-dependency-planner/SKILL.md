---
name: nexus-dependency-planner
description: Analyze enterprise engineering backlogs, architecture dependencies, prerequisites, risk, critical paths, sequencing, and task readiness. Select the highest-value dependency-ready task rather than blindly following backlog order.
---

# NEXUS Dependency Planner

## Purpose

Ensure NEXUS selects engineering work based on dependency readiness, architectural sequencing, risk, and mission value—not merely backlog order or task size.

## When to Activate

Activate when:

- Selecting the next autonomous task.
- Planning a milestone or release.
- Breaking down an architecture initiative.
- Identifying prerequisite work.
- Multiple tasks compete for execution.
- A task appears blocked.
- Delegating work to multiple agents.
- Replanning after implementation discoveries.

## Planning Principles

1. Dependency readiness outranks backlog priority.
2. Security prerequisites outrank convenience.
3. Foundational work should precede dependent functionality.
4. Avoid parallel work with shared-file conflicts.
5. Favor cohesive vertical slices.
6. Keep task boundaries verifiable.
7. Avoid unnecessary architecture expansion.
8. Prefer evidence-based task readiness.
9. Replan when new dependencies emerge.
10. Maintain a continuously executable backlog.

## Task Model

Every meaningful task should identify:

- Task ID
- Title
- Objective
- Business or engineering value
- Priority
- Dependencies
- Affected bounded contexts
- Estimated complexity
- Risk
- Acceptance criteria
- Validation strategy
- Agent assignment
- Status
- Blockers

## Dependency Analysis

For every candidate task:

1. Identify required architecture contracts.
2. Identify required domain models.
3. Identify application use cases.
4. Identify required ports and adapters.
5. Identify database or migration prerequisites.
6. Identify security and authorization dependencies.
7. Identify test infrastructure.
8. Identify integration dependencies.
9. Identify unresolved decisions.
10. Determine whether the task is genuinely executable.

Classify dependencies as:

- `REQUIRED`
- `RECOMMENDED`
- `OPTIONAL`
- `BLOCKING`
- `EXTERNAL`

## Readiness Classification

### READY

All required dependencies exist and the task can begin safely.

### READY_WITH_CONSTRAINTS

The task is executable with explicit limitations.

### BLOCKED

A required dependency, decision, capability, or environment is missing.

### NEEDS_DECOMPOSITION

The task is too large, ambiguous, or difficult to validate as a single unit.

### NEEDS_DECISION

A material architectural or product decision must be resolved before implementation.

## Task Scoring

Evaluate candidate work using:

- Dependency readiness
- Mission impact
- Security impact
- Risk reduction
- Architecture leverage
- User value
- Estimated effort
- Validation feasibility

Scoring must not override hard prerequisites.

Do not select a task merely because it has the highest numerical score.

## Execution Selection

Select work in this order:

1. Critical security issues.
2. Required architectural prerequisites.
3. Blockers affecting multiple tasks.
4. High-value dependency-ready tasks.
5. Cohesive vertical slices.
6. Quality and reliability improvements.
7. Enhancements.

When choosing between tasks of similar value, prefer the task that:

- Unblocks more downstream work.
- Reduces risk.
- Establishes a reusable contract.
- Has clear acceptance criteria.
- Can be independently validated.
- Avoids extensive cross-task contention.

## Parallelization

Before delegating parallel work:

- Identify shared files.
- Identify shared contracts.
- Identify migration conflicts.
- Identify integration dependencies.
- Identify common test infrastructure.

Do not parallelize tasks that depend on each other sequentially.

Use parallel agents for isolated work such as:

- Independent bounded-context implementations.
- Independent test development.
- Documentation and architecture review.
- Security analysis.
- API contract review.

The coordinating agent remains responsible for integration.

## Backlog Maintenance

Keep the backlog:

- Dependency-aware.
- Prioritized.
- Decomposed.
- Updated with discovered work.
- Free of duplicate tasks.
- Explicit about blockers.
- Aligned with the architecture.

When implementation discovers additional work:

1. Record the new task.
2. Establish dependencies.
3. Assign priority.
4. Determine readiness.
5. Re-evaluate the current mission.

## Planning Output

Provide:

- Current mission
- Critical path
- Dependency graph or summary
- Ready tasks
- Blocked tasks
- Newly discovered dependencies
- Recommended next task
- Rationale
- Expected validation

## Completion Criteria

Planning is complete when the selected task:

- Has a clear objective.
- Has identifiable dependencies.
- Is appropriately scoped.
- Has acceptance criteria.
- Has a validation strategy.
- Is ready for execution—or has explicitly documented blockers.

---
name: nexus-autonomous-recovery
description: Recover autonomous engineering work after interruptions, failed runs, context loss, partial execution, agent failures, or inconsistent repository state. Reconcile actual Git, files, CI, issues, PRs, checkpoints, and execution logs before continuing.
---

# NEXUS Autonomous Recovery

## Purpose

Enable NEXUS to resume engineering missions safely and autonomously after interruptions without duplicating work, losing state, or making assumptions about completed tasks.

## When to Activate

Activate when:

- A new NEXUS session starts.
- Context is missing or has been compacted.
- A previous execution stopped unexpectedly.
- A task, agent, test, or tool failed.
- The repository state differs from the recorded checkpoint.
- Git, CI, PR, or issue state may have changed.
- Work is partially complete.
- An execution log indicates unresolved work.

## Recovery Principles

1. Repository state is authoritative over conversational claims.
2. Git state is authoritative over unverified commit claims.
3. CI state is authoritative over unverified test claims.
4. PR state is authoritative over checkpoint assumptions.
5. Never assume an interrupted task failed completely.
6. Never assume an interrupted task completed successfully.
7. Preserve valid work.
8. Avoid duplicate implementations, commits, PRs, or issue updates.
9. Recover before beginning new work.
10. Resume from the safest verified point.

## Recovery Procedure

### Phase 1 — Read Persistent State

Read:

- `NEXUS.md`
- `docs/NEXUS/STATE.md`
- `docs/NEXUS/ROADMAP.md`
- `docs/NEXUS/BACKLOG.md`
- `docs/NEXUS/EXECUTION_LOG.md`
- `docs/NEXUS/CHECKPOINT.json`
- `docs/NEXUS/DECISIONS.md`

Identify:

- Last recorded run
- Active mission
- Active task
- Assigned agents
- Last verified checkpoint
- Known blockers
- Pending validation
- Pending GitHub operations

### Phase 2 — Reconcile Reality

Inspect:

- Current branch
- Working tree
- Untracked files
- Recent commits
- Existing implementation
- Tests
- Open issues
- Open PRs
- PR review status
- CI status

Compare actual state against persistent NEXUS state.

Classify discrepancies:

- STATE_CORRECT
- STATE_STALE
- PARTIALLY_EXECUTED
- UNRECORDED_WORK
- DUPLICATE_WORK_RISK
- BLOCKED
- UNKNOWN

### Phase 3 — Establish Recovery Point

Select the latest safe recovery point.

A recovery point must be based on verifiable evidence such as:

- A clean commit
- A passing test result
- A validated implementation
- A successful integration
- A documented checkpoint

Do not treat an uncommitted or untested implementation as complete.

### Phase 4 — Resolve Inconsistencies

Prioritize:

1. Prevent destructive actions.
2. Preserve existing work.
3. Restore branch safety.
4. Resolve broken or incomplete implementations.
5. Re-run required validation.
6. Update persistent state.
7. Continue the mission.

### Phase 5 — Resume Execution

Resume the interrupted task when:

- It remains valid.
- Dependencies are satisfied.
- The task does not duplicate existing work.
- The current branch is correct.
- Required context is recovered.

Otherwise, re-plan and select the next dependency-ready task.

## Agent Recovery

If a delegated agent failed or became unavailable:

1. Inspect its work.
2. Inspect files it modified.
3. Inspect any commits it created.
4. Run relevant tests.
5. Identify unfinished requirements.
6. Preserve valid output.
7. Reassign only the remaining work.

Never blindly restart a failed agent task if it may have produced usable work.

## Git Recovery

Before Git operations:

- Confirm repository root.
- Confirm active branch.
- Confirm remote configuration.
- Inspect working tree.
- Inspect staged changes.
- Inspect recent commits.

Never:

- Force-push.
- Reset user work.
- Delete branches without authorization.
- Overwrite uncommitted changes.
- Push to `main`.
- Merge a PR.

All autonomous engineering work must remain on the designated feature branch.

## Checkpointing

After every meaningful milestone, update:

- `STATE.md`
- `EXECUTION_LOG.md`
- `CHECKPOINT.json`

Checkpoints should capture:

- Run ID
- Timestamp
- Mission
- Task
- Current branch
- Git commit
- Working-tree state
- Implementation status
- Test status
- Blockers
- Pending operations
- Next action

## Status Integrity

Use precise status labels:

- `PLANNED`
- `STARTED`
- `IMPLEMENTED`
- `TESTED`
- `VERIFIED`
- `COMMITTED`
- `PUSHED`
- `PR_UPDATED`
- `CI_PASSED`
- `BLOCKED`

Never use a higher status without evidence.

## Completion Criteria

Recovery is complete when:

- Repository reality has been reconciled.
- No valid work has been discarded.
- Duplicate work has been avoided.
- The recovery point is established.
- Persistent state has been updated.
- The next action is clear.
- Execution has resumed when safe.

## Required Recovery Report

Provide a concise report:

- Recovery run ID
- Previous checkpoint
- Actual repository state
- Discrepancies identified
- Work preserved
- Work recovered
- Validation performed
- Current blockers
- Next task

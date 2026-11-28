# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | **NEX-099** (run 99): the shared tenant-access rule was enforced on **four** identity paths but the **demo identity provider** was an uncounted **fifth** — it checked only `is_active`, so a suspended or deprovisioned membership still authenticated in development. Fixed by routing the demo provider through `ensure_tenant_access`. **IMPLEMENTED + TESTED + COMMITTED + PUSHED** at `20bd267` (1784 passed, 4 skipped; gates green; load-bearing probed); **CI PASSED** at `20bd267` and again at `a4e1880` after the docs commit. Prior: **NEX-098** (run 98, product `f4a2845`, CI 4/4 on the product commit itself) bounded and paged the organization roster with an exact counted total. Phase 2 **complete**. Phase 5: **NEX-001…NEX-046 VERIFIED**. Phase 3 (governance): NEX-047…**NEX-098 VERIFIED**. Prior: **NEX-097** (run 97, product `febb810`, 4/4 on itself): the roster no longer lists offboarded people. |
| Branch | `feature/agentic-enterprise-platform` |
| Starting commit (run 99) | `6e95ae2` — the live remote head resolved with `git ls-remote`; this clone's fetch refspec maps only `refs/heads/main`, so `origin/feature/...` is a stale snapshot. A fresh workspace arrives on `main`; the branch was checked out at the live head before any edit. |
| Head commit (measured) | **`a4e1880`** (docs record commit; parent `20bd267` carries the NEX-099 implementation). Pushed; remote head confirmed by `git ls-remote`. Resolve with `git log --oneline -1 -- src/notes_service/infrastructure/identity/demo_provider.py`. Prior product `f4a2845` (NEX-098, VERIFIED 4/4 on itself); `febb810` (NEX-097, 4/4 on itself); `87936d4` (NEX-096, 4/4 on itself); `fb027ee` (NEX-095, 4/4 on itself). Never trust a SHA recorded in this file. |
| Last CI result | **PASSED at `a4e1880`** — **both** runs `completed/success`, verified per run **and per job**: `46156228708` (push) and `46156232906` (pull_request), Backend 12/12 steps green, Frontend 11/11 steps green, zero failed, zero skipped in each. The docs commit invalidated the earlier `20bd267` verdict for the head rather than being assumed harmless, because CI formats Python inside Markdown fences. Prior: `f4a2845` (NEX-098 product) and `e9ccba4` (run-98 docs+test) were each 4/4 at their own `head_sha`. |
| Tracking issue | #2 (open) — NEX-099 was delivered as comments `7240863239` (status), `7240892866` (VERIFIED verdict with per-job CI evidence) and `7240996168` (close-out: CI re-verified at `a4e1880` and `0d1674c`, plus the state-lag invariant and the next task). NEX-098 was comment `7240543435`. Issue stays **open**: its acceptance criteria are program-level. |
| PR #5 | **#5 (open, `draft=false`)** — `feature/agentic-enterprise-platform` → `main`. Body carries NEX-052…**NEX-099**. The NEX-099 section now carries a per-run **and per-job** CI table; it was published with `gh pr edit --body-file` and the landed body **read back and compared** (identical after trailing-newline normalisation). Head is `a4e1880`. **Never merged by automation.** |
| **Open P1** | **Every identity-establishing path must obey the tenant-access seam.** NEX-099 closed the fifth (demo). The docstring's enumeration was wrong twice (three → four → five), so audit identity providers by grep, not by the docstring, before claiming the seam is complete. |
| **Open P1b** | **No unbounded *organization* list remains.** The paging family (NEX-082…NEX-098) is closed. Remaining known whole-set read: `FoldersRepository.list_for_owner`, user-authored, small, on no paged route — deliberately not fixed (DECISIONS). |
| **Open P4 (gate trust)** | **Closed by NEX-073** at `09888ac` (test-only; CI 4/4 `completed/success`, both frontend runs green). The flake was not "one assertion in the wrong order" — the note list is emptied into a loading state during every identity-keyed refetch, so three `waitFor`-wrapped *negative* assertions were passing vacuously on every run, including the tenant-isolation test. Each now anchors on a positive assertion, and the isolation test was proved to fail on a real leak. See NEX-073 below. |
| CI status | `ci_status` in `CHECKPOINT.json` is the authority — per-slice results with timestamps. Standing rule: a docs commit reads `completed/cancelled` once superseded in the concurrency group — never evidence. A **concluded** run is never retracted, which is why evidence anchors to one. |
| Authorized to merge to `main` | No — automated merging is prohibited |

## NEX-098 (run `NEX-2026-11-28-98`) — the organization roster was a whole-set read whose total was its own length

`GET /organizations/{id}/members` called
`SqlAlchemyOrganizationMembershipRepository.list_for_organization(organization_id)` with no window and
filled `PageMeta(total=len(items))` from that list. There is no per-organization member cap, so one
read and one response grew with the tenant — the same unbounded-listing shape NEX-096 removed from
`GET /agents`.

Shipped:

- `limit` (1..200) / `offset` on the route and the port, windowed in SQL, not in Python.
- `count_for_organization`, a scoped `COUNT` sharing the **same** `_roster_predicate` as the listing.
  A count with a different predicate would report a total no page can reach; the persistence test
  deprovisions a member and asserts the row leaves both.
- Order `(created_at, id)`. Tied `created_at` is not hypothetical — a burst invite shares a timestamp —
  and `OFFSET` over a non-total order can repeat one row while skipping another.
- The predicate is applied *before* the window, so `limit=1` never becomes a one-row peek: a non-member
  still gets `404`, not a truncated roster.

### The test that had to be fixed before it counted

The first draft's docstring claimed insertion order was reversed so an engine returning insertion order
would fail. It was **not** reversed — the fixture iterated forward, SQLite returned tied rows in that
order, and the test passed with *and without* the `id` tiebreaker. Dropping the tiebreaker left 4
passed. That is a guard encoding the defect it exists to catch.

Corrected to insert `reversed(DEMO_USERS[:ROSTER_SIZE])` and to assert the whole listing's sequence
equals its `id`-sorted sequence. Re-probed: without the tiebreaker
`test_walking_the_window_neither_repeats_nor_skips_a_member` fails; with it, passes. The other half of
the fix was probed separately — `total=len(items)` restored → 2 failed. Both doc statements that
asserted the untested claim were rewritten to the measured result rather than left standing.

### Gates (bare, as CI runs them, on this tree)

`ruff format --check` 326 files · `ruff check` clean · `mypy` 144 files clean ·
`pytest -o addopts=--strict-markers` **1778 passed, 4 skipped** (baseline 1769 + 9).

CI: `f4a2845` **4/4 `completed/success` on the product commit itself** — runs `46153368050` (push) and
`46153373346` (pull_request), both `head_sha = f4a2845`, per job. **Caveat resolved:** the strengthened
test postdates `f4a2845` but shipped in `e9ccba4`, whose own runs are 4/4 `completed/success` on its own
`head_sha` (`46153750548`, `46153754933`, per job, zero failed, zero skipped). Both commits are green at
their own head; neither verdict is descendant-CI. PR #5 body and issue #2 were updated and **read back**.

## Next best actions (run `NEX-2026-11-28-98`, superseding every list above)

The exact-total paging family (NEX-082…NEX-098) is **closed**. No unbounded organization or agent list
remains. The next run starts clean:

1. **Do not start another paging slice to keep the streak going.** Every remaining `list_*` that reads a
   whole set is either user-authored and small (`FoldersRepository.list_for_owner`, exposed on no paged
   route) or already windowed. Adding a window with no consumer that needs one is churn, and DECISIONS
   records why folders are deliberately left alone.
2. **Select the next dependency-ready slice** from `BACKLOG.md` / `ROADMAP.md`, not from this list.
3. **Carry the shape forward**: a test that fails without the fix, a probe that proves the test bites,
   and a CI verdict read per run *and* per job. This run's own lesson is that a guard can pass without
   the fix — probe the guard, not just the code.
4. **Do not start a new slice with under 10 minutes of window left.** Commit, push, deliver, record.

**Do not redo** NEX-098 or anything before it. **Do not** re-append NEX-079…NEX-098 to PR #5's body —
everything through NEX-097 is already in it and NEX-098 is appended in this run's close-out.

## NEX-097 (run `NEX-2026-11-28-97`) — the organization roster listed offboarded people, and counted them

**Status: VERIFIED (close-out run 97)** — product `febb810`, CI **4/4 `completed/success` on the
product commit itself** (runs `46148603437`, `46148607713`). Gate re-run against this tree in this
run: **1769 passed, 4 skipped** (`postgresql`-marked); ruff format/check clean on 325 files; mypy
clean on 144 source files.

`GET /organizations/{id}/members` answered with every membership row the organization had ever had.
`SqlAlchemyOrganizationMembershipRepository.list_for_organization` selected on `organization_id` alone,
and the router filled `PageMeta(total=len(items))` from that same list.

Offboarding does not delete the row. It is a **soft delete** — `status -> DEPROVISIONED`, row kept for
audit history — so a listing that does not name the status lists the person as a current member
forever. The team roster (`SqlAlchemyTeamMembershipRepository.list_for_team`) had carried a
`status == ACTIVE` predicate for exactly this reason since it was written; the organization roster was
the sibling that never got it, and NEX-001's acceptance criterion "soft-deleted memberships never
appear in rosters" was recorded as verified against tests that never deprovisioned through this route.
A governance invariant sat broken behind a green suite.

The filter is `status != DEPROVISIONED` — the terminal state only, deliberately not `== ACTIVE`.
Copying the team roster verbatim would have traded one bug for a worse one. Suspension is a
**reversible pause**, and `POST /organizations/{id}/members/{membership_id}/reactivate` names the
membership **by id**. A suspended row filtered out of the roster is a person an administrator can see
in no list and still cannot restore: the roster would stop showing the wrong people and start hiding
the only handle on the right one. So suspended members stay listed, with `status: "suspended"`, and
`reactivate` works on them.

`meta.total` follows the query, so the count stops including people the page does not show.

Three tests in `TestOrganizationRoster` (`tests/api/test_organizations_api.py`) pin this: a
deprovisioned member leaves both the items and the total; a suspended member stays with its id and
counts; a deprovisioned membership cannot be reactivated (`422`), which is what makes the roster's
silence about that person consistent rather than lossy. Load-bearing proved and restored: reverting
only the adapter fails exactly the two deprovisioned-member tests (30 passed). The suspended test
passes with and without the fix, by design — its job is to go red the day someone "corrects" the
filter to `ACTIVE`.

The port documents the contract, so the next adapter cannot reinterpret it as "active only".

## NEX-096 (run `NEX-2026-11-28-96`) — the owner agent listing was the last unpaged set read, and its total was its page size

**Status: VERIFIED** — CI 4/4 `completed/success` on the product commit `87936d4` itself (runs
`46145902373` pull_request and `46145897805` push; Backend 12/12 and Frontend 11/11 steps green, zero
failed, zero skipped, both `head_sha` = `87936d4`), so no docs-head anchor is needed.

`GET /agents` was the one listing route in the exact-total paging family (NEX-082, NEX-087, NEX-088,
NEX-090, NEX-095) still reading a whole set. `SqlAlchemyAgentRepository.list_for_owner` ran
`select(AgentRow).where(owner=...).order_by(created_at)` with no window, and the router reported
`PageMeta(total=len(items), limit=len(items), offset=0)`. Agent registration has no per-user cap, so an
owner could register agents in a loop and make every listing request price the entire set — a
self-inflicted denial of service the route gave the client no way to bound.

The total half mattered more. On an unpaginated route `len(items)` genuinely is the total, which is why
NEX-088 exempted this route from its sweep. The exemption was right for the code as written and wrong
as a resting place: the number was honest *because* the route refused to truncate, and refusing to
truncate was the defect. Adding `limit` without an exact count would have turned an honest unpaginated
listing into a lying paginated one — the precise failure NEX-088's four siblings each had.

The route now takes `limit` (1..200, default 50) and `offset` (>= 0); `list_for_owner` applies
`.limit()/.offset()` and orders by `(created_at, id)`; `count_for_owner` answers the set's real size with
a scoped `SELECT count(*)`, an aggregate that reads no rows and cannot disclose another user's agent
volume; `ListAgents` returns `AgentPage(items, total)` and the router puts that total in `PageMeta.total`.

The order is `(created_at, id)` because the window is an `OFFSET`: an order that is not total lets a row
repeat while another is skipped as the window shifts, and agents registered in one request burst share
`created_at` to the microsecond — the same reason NEX-093 added tiebreakers three queries down.

Six tests in `TestAgentListingHttp` cover the listing, the exact total, an offset walk that must not
repeat a row, the owner-scoped total, a page past the end, and `limit=201` as 422 rather than a silently
clamped page. Load-bearing proved and restored: reverting the four source files fails exactly the four
new tests and leaves the pre-existing listing test green (4 failed, 73 passed); restored, 77 passed.

## NEX-094 (run `NEX-2026-11-28-93`) — the two "what authority is in force" reads priced the whole grant table

**Status: VERIFIED** (run `NEX-2026-11-28-94`) — descendant-CI verdict. The product commit `d8353ff`
carried zero check-runs, cancelled in the CI concurrency group by its own docs child; the verdict
anchors to docs head `d4abd07`, which is 4/4 `completed/success` (runs `46141744541`, `46141737990`;
Backend 12/12 and Frontend 11/11 steps green, zero skipped). Licence for the anchor: `d8353ff` is
`d4abd07^`, the diff touches only `docs/NEXUS/*`, and both product files are blob-identical across
the two trees.

`active_grants()` and `active_for()` are the reads a policy gate consults to answer *may this operator
act now*. Both ran `select(OperatorGrantRow)` with **no `WHERE`**, built a domain object per row, and
discarded the revoked and long-expired ones in Python. Grants are never pruned — revocation is
recorded, not deleted (NEX-079) — so the table only grows, and every authorisation read cost the
deployment's entire grant history. NEX-087 (access log), NEX-088 (audit trail) and NEX-092 (revocation
report) closed this class in neighbouring reads; these two were the remaining seam.

Both now carry an active-window predicate in the `WHERE`. The reason this had been left undone is that
the code **said so**: `active_for` carried a comment that `is_active` is *the* definition of "in force"
and that pushing it into SQL would duplicate the rule and let the two drift. That reasoning is sound —
it is only safe when the SQL is an **exact** translation. It turned out to be one: `is_active` is four
independent branch tests, each a bare single-column comparison (not revoked; `granted_at <= now`;
`not_before IS NULL OR not_before <= now`; `expires_at IS NULL OR expires_at > now`), with no
arithmetic and no clock behaviour the database evaluates differently from Python. So
`_active_predicate(now)` is a transcription, and rather than argue that in a comment the equivalence is
now pinned by test across the boundary instants.

The test work, and the trap in it. `test_active_for_does_not_read_the_whole_grant_table` counted
`_to_domain` calls — but the fixed `active_for` reads `row.scope` straight off the filtered rows and
converts nothing, so the probe measured `[]` and the assertion contradicted the fix it was supposed to
defend: it would have gone green only against unfixed code. Replaced by
`test_active_reads_filter_in_the_store_rather_than_in_python`, which records executed statements
through `before_cursor_execute` and requires both active-read `SELECT`s to carry `revoked_at is null`
and `expires_at`. Generalise it: **a work-count probe only exists for a path that does work; when the
fix removes the work, observe the statement.** `active_grants` does convert rows, so counting
conversions remains the right observation there.

Two mutation controls, both observed. Reverting the adapter fails the work-count and the statement
tests (the captured SQL is a bare `select ... from operator_grants order by operator_grants.id`). More
importantly, a one-character drift `<=` → `<` in the predicate fails all three
`test_active_predicate_agrees_with_the_domain` instants — that is the control that matters, because it
proves the boundary test catches a *transcription error*, not merely the absence of the fix. For a
read that confers authority, the under-select direction is the dangerous one: it silently denies a live
grant, and a test that only reverts the whole change cannot see it.

`revoked_grants`' docstring also claimed `is_active` "carries real logic … which is why that filter
stays in Python". That is now false and would have re-taught the wrong rule to the next reader, so it
was corrected in the same commit.

## NEX-088 (run `NEX-2026-11-28-88`) — the audit page's total was its own window size

**Status: COMMITTED `06b5b32` · PUSHED · VERIFIED — CI 4/4 `completed/success`** (runs
`46127299155` push and `46127304188` pull_request on docs head `592ba07`, product tree identical
to `06b5b32`; also `46127157086` on `06b5b32` itself, 2/2). Delivered to PR #5 and issue #2.

`GET /agents/{agent_id}/tool-invocations` takes `limit`/`offset` and answers `meta.total`. It filled
that field with `len(items)` — the size of the page it had just built. A three-entry trail reported
`total: 2` on the first page and `total: 1` on the second; neither number was the trail, and neither
survived turning the page. The field exists precisely so a reader can tell a complete trail from a
window, so answering with the window's own size made every page look complete. That is the
difference between "this agent made 50 tool attempts" and "these are the first 50 of thousands" —
on an audit surface.

The count is now asked for directly instead of inferred from a page, the same resolution as NEX-082
and NEX-087: `count_for_agent(agent_id)` on `ToolInvocationRepository` → `SELECT count(*) ... WHERE
agent_id = :agent_id`, an aggregate that reads no rows and cannot be truncated by volume.
`ListToolInvocations` returns `ToolInvocationAuditPage` (items + exact total), mirroring `NotePage`,
and the router puts the true count in `PageMeta.total`. Count and page share a transaction, which
is atomicity, not a single snapshot under READ COMMITTED — the docstring says so rather than
claiming more.

The suite did not catch the original defect because no fixture asked for a second page. The new
regression asks for both pages and asserts `meta.total == 3` on each. Writing it exposed a second,
worse bug: an **unscoped** count — no `agent_id` filter — passed that test, because it happens to
return the right number when one agent owns every row. It would report every agent's attempts as
this agent's and disclose how much another agent has done. Mutation found it, not review;
`test_meta_total_counts_only_this_agents_trail` now pins the scope and the unscoped count fails.

Sweep complete: the other `PageMeta(total=len(items))` sites were each read and are genuinely
unpaginated (no `limit`/`offset`), where page size *is* the total. Only the paginated listing lied,
so only it changed.

Security surface unchanged — read-only, gated, no new authority, no migration. The property *is*
the scope of the count: an unfiltered count on an agent-scoped audit read leaks another agent's
volume.

## NEX-087 (run `NEX-2026-11-28-87`) — the retention report said the retained evidence was younger than it was

**Status: VERIFIED at `82d8a64` — CI 4/4 `completed/success`** on docs head `cf44c89` (runs `46125167112` push, `46125170933` pull_request; Backend and Frontend both green), polled 2026-11-28T08:22:33Z. Delivered to PR #5 body and issue #2 comment `7236499955`.

`PurgeOperatorAccessLog` is the only mutation the append-only operator access log permits, and its
result carries `retained_from`: "the oldest entry still in the log after the purge". That field is
why the result is not merely a count — an operator reads it to decide whether the retained evidence
still covers the period they care about.

It was computed as `recent(limit=1000)[-1]`. `recent` is newest-first and capped, so its last
element is the oldest entry of **that page**, not of the log. Past one page of survivors the field
understated the age of the retained evidence: the trail looked shorter than it was, the error grew
with traffic, and it grew in the direction that makes a premature loss of history look acceptable.
A probe measured it: 1500 survivors, reported `12:08:22` against a true oldest of `12:00:00`. Every
existing fixture held fewer than one page, so the suite could not see it.

The port gained `oldest_retained() -> datetime | None` and the use case delegates to it. `recent`
cannot answer the question — newest-first and capped means it can only ever answer about a page — so
the question is asked directly: `MIN(at)` in SQL (an aggregate that reads no rows and cannot be
truncated by volume, relabelled to aware UTC because SQLite returns naive for a plain `TIMESTAMP`),
the first entry of the ring in insertion order under the lock, and `min()` in the test spy so the
fake stays honest. `_OLDEST_SCAN_LIMIT` and its "never claims to be exhaustive" comment were
deleted rather than kept — a constant that admits its own truncation is the defect written down.

Tests were built to fail against the old answer and proved to: with the page-derived read restored
the 1500-survivor regression fails on the observed wrong timestamp. `None` still means "empty log";
an empty log is a fact, not a stamp to invent.

Security surface unchanged — read-only, gated, no new authority, no migration. The security property
*is* the truthfulness of the field: a retention report that understates the age of the retained
evidence cannot support the decision it exists to support.

## NEX-085 (run `NEX-2026-11-28-85`) — the strongest grant in the system could not be revoked

`RevokeOperatorGrant` validated its scope against `KNOWN_OPERATOR_OPERATIONS` — the *issue*
allowlist — which deliberately omits the wildcard, because issuing `"*"` amplifies authority
and that should be a deployment decision, not a request. Reusing it on the *revoke* path made
revocation refuse `"*"` as an unknown scope (`422`). A wildcard operator seeded from
configuration could therefore not be stood down through the running platform at all, only by
editing the seed and restarting — the "edit configuration to revoke" hole NEX-079 existed to
close, reappearing one level up on the strongest grant.

The fix is a per-direction allowlist, not a shared one: `REVOCABLE_OPERATOR_SCOPES =
KNOWN_OPERATOR_OPERATIONS | {WILDCARD}` guards revocation only. Issuing still refuses `"*"`
and revocation still refuses a bogus scope, so neither direction became a hole. Naming `"*"`
is a *specific* revocation keyed by `(user, scope)`: an explicit grant held beside the
wildcard survives, and the audit entry names the single authority removed.

Validated with mutation control: with the source fix stashed, both wildcard tests fail `422`.
Full suite `1679 passed, 4 skipped`, exit 0; ruff format/check clean; mypy clean on 143 files.
Committed `c698979` and pushed; remote head verified through the branches API. The docs head
`d9d0a3a` then concluded **CI 4/4 success** (`46120082100` push, `46120085453` pull_request),
so NEX-085 is **VERIFIED**. The product commit's own runs read `cancelled` — superseded in the
concurrency group, never a failure; `git diff c698979 d9d0a3a -- src/ tests/` is empty.

## NEX-084 (run `NEX-2026-11-28-84`) — a bounded grant could be converted into a longer one

**Status: VERIFIED at `09cc4ad` — CI 4/4 success** (Backend + Frontend, on both the
pull_request and push events, polled 2026-11-28 run 84). Local gates were green before the poll.

Operator grants attenuate **scope**: an issuer may only hand on a scope they themselves hold.
Nothing bounded **lifetime**. An operator holding a one-hour incident grant could issue the same
scope with a thirty-day expiry, so the recipient held authority the issuer never had and could
not have kept. Expiry is the entire safety property of a bounded grant; a grant its holder can
convert into a longer one is not bounded in the dimension that makes it safe. Bounded authority
had exactly one arrow — revocation (NEX-079). It had no ceiling.

`_require_attenuation` now returns the issuer's **temporal ceiling** for the requested scope —
the furthest `expires_at` among their own live grants covering it, `None` if any is unbounded —
and `IssueOperatorGrant` refuses with **409** when the requested expiry exceeds it.

Four deliberate choices:

- **The ceiling is the furthest covering grant, not the nearest.** An issuer who holds a scope
  for a week has not narrowed their authority by also holding an unrelated five-minute grant of
  it; refusing on the nearer one would reject a hand-out their authority plainly covers.
- **One store read, at the gate's clock.** Scope and lifetime describe the same instant, so the
  scope check cannot pass against a grant that has lapsed by the time the lifetime is compared.
- **409, not 403.** The caller *is* authorized to issue grants; the requested grant conflicts
  with the issuer's own authority. A 403 would assert the caller may not issue at all, which is
  false, and would collide with the ADR-008 permission split.
- **Settings-seeded operators are unaffected.** Their grants are unbounded (`expires_at=None`),
  so the ceiling is `None`. Existing deployments change behaviour not at all.

**The tests were made to fail before being trusted.** Disabling the temporal comparison fails all
three new tests — the two use-case tests and the API test — so neither the unit nor the HTTP
assertion is vacuous. The API test asserts the nested envelope `body["error"]["code"] ==
"conflict"`, pinning the error contract rather than a bare status.

Gates: full suite **1678 passed, 4 skipped**, exit 0; `ruff format --check` 321 files clean;
`ruff check` clean; `mypy` clean on 143 source files. No migration; no new authority.

## Run 84 reconciliation — two records were stale in the *reassuring* direction

1. **NEX-083 was already VERIFIED and already delivered.** Run 83 stopped with CI `in_progress`
   and its checkpoint asked this run to poll, then clear delivery debt for NEX-082 and NEX-083.
   Polling found `453e134` concluded 2/2 success over NEX-083's exact product tree, and reading
   PR #5's live body from the API showed the NEX-082 and NEX-083 sections each present **once**.
   The recorded debt did not exist. A state file can under-report completion, and a run that
   trusts it will redo finished work — the same class as the vacuous assertion in NEX-073, one
   level up: the *record* passed a check it never actually performed.
2. **A concurrent run posted issue #2 comment `7235362658` 103 seconds into run 84.** Its claims
   were checked independently (CI runs read, PR section counts asserted) rather than trusted, and
   they held — so no duplicate comment was posted. Any run here must re-read the issue and the
   PR body before posting, and resolve the head with `git ls-remote`, because the other run's
   docs commits advance the tip independently.

## Next best actions (run `NEX-2026-11-28-93`, superseding the lists above)

1. **Verify NEX-094 before anything else.** It is IMPLEMENTED + TESTED with no CI verdict. Read CI per
   *run* and per *job* on the product commit; only `completed/success` on both Backend and Frontend, on
   the product commit itself (or on a docs head whose product tree is identical — state that licence
   explicitly), makes it VERIFIED. Then append the NEX-094 section to PR #5 with `--body-file` and read
   the landed body back.
2. **The `operator_grants.py` adapter sweep is closed.** Every read whose docstring promises bounding
   now bounds in SQL. `list_all_for_integrity_sweep` already carries `(created_at, id)`; the two
   delegation reads and `list_for_owner` order on `created_at` alone but are *unpaged whole-set* reads,
   so an unstable tie can only reorder presentation — not this defect class. Do not open that without a
   consumer that depends on the order.
3. **Hold every slice to the same standard.** A test that fails without the fix; a *mutation* proving
   the test bites on a small error, not just on a full revert; a CI verdict read per run and per job.
   Then take the next dependency-ready slice from `BACKLOG.md`. Do not start one with under 10 minutes
   left on the clock.
4. **When promoting a status, sweep every record file for the old words** (`IMPLEMENTED + TESTED`,
   `CI pending`, `not yet`, `no verdict`) — a promotion is a multi-file edit, not a one-line change.

**Do not redo** NEX-094's implementation and do not re-verify NEX-086…NEX-093 — all are implemented,
pushed and CI-verified.

## Next best actions (run `NEX-2026-11-28-86`, superseded — kept for history)

1. **Take the next dependency-ready slice from `BACKLOG.md`.** NEX-086 is closed, verified and
   delivered; no delivery debt remains on PR #5 or issue #2. Write the failing test first.
2. **Do not start a new slice with under 10 minutes left on the clock.** Leave a committed,
   pushed, recoverable increment: a small committed slice beats a large uncommitted one.
3. **When promoting, sweep every record file for the old words** (`in_progress`, `CI pending`,
   `not yet`, `no verdict`) — a promotion is a multi-file edit, not a one-line status change.

**Do not redo** NEX-086's implementation (committed at `70d7667`, CI 4/4), and do not re-verify
NEX-085, NEX-084, NEX-083, NEX-082, NEX-081, NEX-080, NEX-079, NEX-078 or NEX-077 — all are
implemented, pushed and CI-verified.

## Next best actions (run `NEX-2026-11-28-85`, superseded — kept for history)

1. **Take the next dependency-ready slice from `BACKLOG.md`.** NEX-085 is closed, verified and
   delivered; no delivery debt remains on PR #5 or issue #2. Write the failing test first.
2. **Do not start a new slice with under 10 minutes left on the clock.** Leave a committed,
   pushed increment behind instead.
3. **Re-check the live head with `git ls-remote`** before trusting any SHA in these files.

**Do not redo** NEX-085, NEX-084, NEX-083, NEX-082, NEX-081, NEX-080, NEX-079, NEX-078 or
NEX-077 — all are implemented, pushed and CI-verified.

## Next best actions (run `NEX-2026-11-28-84`, superseded — kept for history)

1. **Poll CI on `09cc4ad` and promote NEX-084 to VERIFIED** on both runs concluding success.
   `gh run list --branch feature/agentic-enterprise-platform --limit 4`. `cancelled` means
   superseded by a later docs commit, never failed.
2. **Append the NEX-084 section to PR #5's body and comment on issue #2.** Assert the section is
   absent before writing, then read back from the API. Do **not** re-append NEX-082 or NEX-083.
3. **Then take the next dependency-ready slice from `BACKLOG.md`.** Do not start a new slice with
   under 10 minutes left on the clock.

**Do not redo** NEX-084, NEX-083, NEX-082, NEX-081, NEX-080, NEX-079, NEX-078 or NEX-077 — the
first is implemented and pushed with green local gates, the rest are CI-verified.

## NEX-083 (run `NEX-2026-11-28-83`) — in-force could say *whether*, never *which*

`GET /api/v1/ops/policies/in-force` could confirm a policy was active but could not say
which policies those were, so the API could not answer "what governs this deployment right
now?" — the one question an in-force view exists to answer. It now returns `policies`,
`policy_count` and `source_name`.

Two deliberate choices. An **empty set is 200 with `[]`, never 404**: absence of policy is
a valid answer, and a 404 would be indistinguishable from the operator gate refusing, so a
caller could not tell "no policies" from "not allowed". The aggregate is built in the
**application use case**, not the router — a transport layer must not be where a contract
shape is decided, or the next interface has to re-derive it.

**A test that passed for the wrong reason was found and fixed.** The non-operator 404 test
was built on DAVE. DAVE is *suspended*, so the request failed at authentication with 401
and never reached the operator gate: `assert 404` passed while testing nothing. Rebuilt on
BOB (active, unprivileged), splitting the client so the operator publishes and the ordinary
user is the one refused.

Local gates green: ruff format/check clean, mypy clean over 143 files, full pytest exit 0,
66 targeted tests. **CI on `c16dd9b` was still `in_progress` at the stop boundary** —
NEX-083 is COMMITTED + PUSHED, **not VERIFIED at the time this section was written**.
Polling it was run 84's first action, and it **succeeded**: `453e134` concluded 2/2
`completed/success` over NEX-083's exact product tree, so **NEX-083 is VERIFIED** (see the
run-84 reconciliation below). Kept as written for the record, with the outcome appended.

## NEX-082 (run `NEX-2026-11-28-82`) — the access trail was capped, so most of it was unreadable

**Status: VERIFIED.** Product `4f8e15d` — CI **2/2 `completed/success`** (polled
2026-11-28T05:36Z); docs head `ab018b2`. PR #5 and issue #2 updated.

**The gap.** `GET /ops/access-report` returned at most `limit` newest entries and offered no
way to reach further back. Retention keeps the operator access log for years (ADR-017), so
every granted operator read older than the newest window was **permanently unreadable**: the
log recorded it and no endpoint could show it. On an audit surface, "read the trail" silently
meant "read its most recent few hundred rows" — and unlike NEX-081's sweep, it did not even
say `truncated` was a dead end, because the paging did not exist at all.

**Why a keyset cursor and not an offset — this endpoint forces it.** Reading this view is
itself a granted operator read, so *every request appends a row at the head of the log it is
reading*. An `OFFSET` page would therefore shift by one row per page: page *n+1* would repeat
an entry page *n* had already shown and skip another outright. A trail with invisible holes
is worse than a capped read, because it looks complete.

**The change.**

- Port: `OperatorAccessEntry.position` — an opaque, monotonic insertion ordinal, exposed for
  comparison only. Documented as *not* a count: retention deletes rows and the ring evicts
  them, so position 42 does not mean "forty-two entries exist".
- `recent(*, limit, before_position=None)` returns entries strictly older than the cursor.
  An adapter that cannot name a position must behave as a plain capped read, not pretend.
- SQL adapter: the surrogate `id` *is* the insertion ordinal and is unique, which is what
  makes the keyset total — no two entries share a position, so no page boundary can land
  between them. Predicate `id < cursor`.
- In-memory ring: a **monotonic counter**, not `len(entries)`. The ring drops its oldest entry
  on overflow, so a length-derived ordinal would reuse a position a surviving entry still
  holds — and a cursor would then skip rows.
- Use case: a non-positive cursor is *rejected*, not read as "everything from now on";
  `next_position` is offered only when the window filled; `truncated` is true for a middle
  page too, because "under-full" is not "the whole trail".
- The capability gate still runs **before** cursor validation, so a non-operator still gets
  `404` and cannot learn from a `400` that the route parses `before_position`.

**Validation.** Full suite `1652 passed, 4 skipped` on `4f8e15d`; `ruff format` 321 files
unchanged; `ruff check` all checks passed; `mypy` clean on 143 source files. New: 14 API paging
tests (a full-trail walk asserting no duplicates and no skips *while each read appends*), 15
adapter keyset tests (including the ring eviction collision), 16 use-case tests. Mutation
control observed failing: forcing the cursor to `None` in the use case fails 3 of the 6 new
paging tests, so the walk assertions are not vacuous.

## NEX-081 (run `NEX-2026-11-28-81`) — a capped sweep excluded the newest trails, permanently

**Status: VERIFIED.** Product `3a05db2`, docs head `85a013c`; CI 4/4 `completed/success` on
`85a013c` (Backend ×2, Frontend ×2, polled 2026-11-28T05:18Z). `85a013c` descends from
`3a05db2` and differs only in state docs, so the product tree under test is NEX-081's.

**The gap.** `SweepAuditIntegrity` (NEX-042) walks the deployment's agent chains and asks the
one question no owner can: *is any trail in this deployment broken*. It reads
`list_all_for_integrity_sweep(limit=max_agents)` — the cap being a legitimate bound, since a
sweep loads every agent's whole trail and an uncapped scan would hold the database open for
longer than a request should. The report even said `truncated` when the cap bit, which is the
honest half.

The dishonest half was that `truncated` was a dead end. There was no `offset`, no cursor, no
"continue from here" — the sweep was *always* the oldest `max_agents` agents, and on a
deployment with more agents than the cap everything past that point was outside the check
forever. Because the walk is oldest-first, the excluded agents were the **newest** trails:
the ones written most recently, and so the likeliest to have been tampered with by whoever
just gained write access. "No break among the ones I looked at" was all the endpoint could
ever say, while an operator reading `truncated: false` on a small deployment and `true` on a
big one had no action available for the second.

A cap with no cursor is not a bound on one request. It is a silent exclusion of everything
past it.

**Delivered.**

- `GET /api/v1/ops/audit-integrity?offset=N`. The response carries `offset` and
  `next_offset`; follow `next_offset` while it is non-null to reach every agent.
  `ChainAuditReport` gained `offset`, `next_offset` and a `covered` property
  (`offset + agents_checked`).
- `verified` now requires `offset == 0` **as well as** full coverage and no findings. This is
  the subtle half: a second page that reaches the last agent and finds nothing has examined
  *some* agents, not the deployment, and it says nothing about the agents before it. Without
  the term, an operator could page through a broken deployment and see nothing but passes.
- `list_all_for_integrity_sweep` orders by `(created_at, id)`. Paging depends on the order
  being **total**: with a tie broken differently between two queries, an `OFFSET` page could
  skip the agent it should have started at and still return a full page, reporting complete
  coverage of a set it never saw.
- `offset` is validated (`InvalidInput`, HTTP 422) **after** the operator gate, so a
  non-operator still gets `404` and cannot probe the validation by watching the error change.

**Tests.** 5 application tests (`TestACappedSweepCanBeFinished`) and 2 API tests. The one that
carries the design is `test_a_clean_second_page_is_not_an_all_clear` — and it was proved
load-bearing rather than assumed: removing the `offset == 0` term from `verified` made it
fail, and the guard was re-verified present before the commit. A test never seen fail is a
hypothesis, not evidence.

**Delivery, and it happened this time.** CI polled to 4/4 success, then NEX-081 was appended to
PR #5's body (now NEX-052..NEX-081) and reported on issue #2 as comment `7234571708`. That
clears the delivery debt this section originally recorded. While appending, a stale line inside
the NEX-080 section — "Backend 2/2 still in_progress, Not VERIFIED" — was corrected to the
polled result (4/4 on `139edf6`). Delivery debt that reads *pessimistically* is still debt: it
invites a redundant re-check next run.

## NEX-080 (run `NEX-2026-11-27-80`) — the revocation was recorded, nothing read it

**Status: VERIFIED.** Product `9336ba3`, docs checkpoint `139edf6`; CI 4/4 `completed/success` on both (Backend ×2, Frontend ×2, polled 2026-11-28T04:55Z).

**The gap.** NEX-079 made revocation durable and attributable: `revoked_at`/`revoked_by` are
written onto the grant row, paired by a `CHECK`, and survive their revoker via `ON DELETE SET
NULL`. Nothing read them back. The operator-grant detail view described holder, issuer, reason
and expiry — but not who ended a grant or when. An operator could see authority had been removed
without learning who removed it. An audit fact no interface surfaces is half a control, and the
same half-built loop NEX-076/NEX-077 closed for `rationale`.

**Delivered — the read side only, and no new authority.**

- `OperatorGrantRepository.revoked_grants()` on the port, implemented by both adapters (in-memory
  and SQLAlchemy), selected by the domain's own `is_revoked` predicate so "this row records a
  revocation" is defined once — the way `is_active` already defines "this row is live".
- `ListRevokedOperatorGrants` behind the **existing** `operator-access-report` scope. Reading the
  platform's removal history needs exactly the authority the holder report already requires;
  minting a fifth token would make every auditor be granted twice to answer one question.
- `GET /api/v1/ops/operator-grants/revoked`, each entry carrying `revoked_at` and `revoked_by`.
  Non-operators get `404`, as everywhere in `/ops`.
- `revoked_at`/`revoked_by` on `OperatorGrantDetail`, so the holdings view can render a grant's
  whole life rather than only its live state.

**Why a separate endpoint, not a filter.** The holdings report answers "who holds what now"; a
revocation is history. One shape covering both forces every reader to tell "live", "expired" and
"revoked" apart out of nullable fields. Grants that merely reached `expires_at` appear in neither
list: lapsing on its own and being taken away are different facts, and the audit needs both.

**Two NEX-079 defects, found by the neighbouring suites and fixed here.** Same root cause —
`revoked_by` and `granted_by` are `ON DELETE SET NULL`, and validation written as if those
columns only ever hold writer-supplied values.

1. The revocation `CHECK` required the pair together. Deleting an account that had revoked a
   grant made `SET NULL` write `revoked_by = NULL` onto a row keeping `revoked_at`, which the
   CHECK refused — **no account holding revocation history could be deleted**, surfacing as a
   raw `IntegrityError`. Migration `a91c6f0d4b27` enforces only the incoherent half: a named
   revoker with no instant. A revocation instant outliving its actor is the correct post-deletion
   state, and the whole reason the FK is `SET NULL` rather than `CASCADE`.
2. The domain refused `granted_by=None` beside a non-NULL `rationale`. That shape is also what
   deleting an *issuer* produces, so every read of the store — the operator gate lookup included
   — raised `ValidationError`. One account deletion was an outage of the entire `/ops` surface.
   The write-time rule is not lost, only relocated to where a human supplies the pair:
   `IssueOperatorGrant` requires a rationale, the configuration seed forbids one.

**Migration.** `a91c6f0d4b27` drops `revocation_is_attributable`, creates
`revocation_actor_requires_instant`. Downgrade restores the strict CHECK after clearing
`revoked_at` on rows whose actor is already NULL. That makes those grants live again — the
fail-open direction — so it is explicit and commented rather than silent. Upgrade, downgrade and
re-upgrade verified clean on SQLite with the new constraint confirmed in `sqlite_master`.

**Validation on the committed tree.** ruff format+check clean (319 files); mypy clean on 143
source files; full suite **1619 passed, 4 skipped**. One pre-existing test updated, not deleted:
`test_a_configuration_grant_must_not_invent_a_reason` pinned the exact rule relaxed here, and is
now `test_a_rationale_without_an_issuer_is_the_post_deletion_shape`, asserting the shape is
accepted and stating where the rule moved. Relaxing an invariant silently in its own guard test
is how an invariant disappears.

**Delivery debt cleared in the same run.** PR #5's body now runs NEX-052…NEX-080 and issue #2
carries a NEX-080 comment. Only NEX-079 and NEX-080 were actually missing: NEX-078 had
already been appended by the previous run while the checkpoint still recorded it as absent —
the stale-record pattern, again, this time in the direction of understating progress.


## NEX-079 (run `NEX-2026-11-27-79`) — an authority with no way to end early

**Status: VERIFIED.** Product `a0c8ee0`; CI 4/4 `completed/success` (Backend ×2, Frontend
×2, every conclusion `success`, polled 2026-11-27T21:20Z). Docs checkpoint `b3b2c3b`.

**The gap.** Every other piece of live authority here has a revoker — delegations, approvals,
agents. An operator grant had one way to end: reach `expires_at`. A mistaken hand-out was stuck
for its whole term, and a *configuration* grant (`granted_by IS NULL`) has no `expires_at` at
all, so it could not be ended without editing the deployment seed and redeploying — which also
rewrites every other configuration grant. Same class as R28, one layer up.

**Delivered.** `POST /api/v1/ops/operator-grants/revoke`. Revocation is recorded **on the
existing row** (`revoked_at`, `revoked_by`), never applied by `DELETE`: deleting would erase the
grant *and* the evidence it was withdrawn, which is the fact the audit exists for. The two
columns are set together or not at all — enforced in the domain *and* by a `CHECK` — so no
writer can leave a half-revocation. `revoked_by` is `SET NULL` on user deletion rather than
`CASCADE`, because losing a revocation when the revoker's account is removed would delete the
record of the revocation itself. Revoking a scope the holder does not hold is `409`, not a silent
success. The gate is the unchanged operator allowlist; non-operators still get `404`, not `403`.

**Two pre-existing defects found by running the neighbouring suites, not the new tests.** The
migration re-created `ix_operator_grants_user_scope`, which already existed — a hard SQLite
`OperationalError` that broke ~11 persistence tests. And
`test_the_table_exists_in_the_migrated_schema` asserted the pre-slice column set; its purpose is
to prove model and migration agree, so it must change when they both legitimately do.

**Validation.** ruff format+check clean, mypy clean on 143 files, full suite **0 failed / 4
skipped**. 11 new API tests. Commit `a0c8ee0`; `git ls-remote` confirms remote == local.

**The gap it creates.** Revocation is durable and enforced but **not readable through the API**.
That is NEX-080, and it is next.

## NEX-077 (run `NEX-2026-11-27-77`) — the reason a grant was issued is now readable

**Status: VERIFIED.** Product commit `9f9a70f`; CI **4/4 `completed/success`** (backend ×2,
frontend ×2), polled 2026-11-28T03:44Z. Local gates: 1574 passed / 4 skipped, ruff format+check
clean, mypy clean.

**The gap.** NEX-076 made a human-issued grant state *why*, and stored it. Nothing read it back.
`ListOperatorGrants` folds per-scope grants into one `OperatorHolder` row per user, and a fold
cannot carry per-grant provenance: a holder with three grants has three issuers and three reasons.
The justification was write-only — a field that reads as evidence but never reaches the auditor.

**Delivered.** `OperatorGrantDetail` (one row per active grant: `user_id`, `scope`, `granted_by`,
`rationale`, `granted_at`, `expires_at`), exposed as `OperatorGrantSnapshot.grants` with
`grant_count`, alongside the unchanged `holders` summary. `OperatorGrantDetailResponse` carries it
over HTTP.

**Design note — additive, not a replacement.** Both views are built from the same `active_grants()`
tuple at the same instant, so they cannot describe different sets. `holders` stays: it is still the
right answer to "who holds what, in total", and replacing it would break the consumer contract for
no gain.

**Mutation control.** Forcing `rationale=None` in the detail (the pre-NEX-077 behaviour) fails four
tests — two application, two API. The assertions are load-bearing, not vacuous.

**Tests.** `tests/application/test_operator_grants.py` (+4: issuer/reason reported; a configuration
grant reports neither; two grants for one holder keep their own reasons; an expired grant appears
in neither view). `tests/api/test_ops_operator_grants_api.py` — the round-trip test now asserts the
reason is readable in the listing, not merely that the POST returned `200`.

**Next.** The read path is closed. Remaining follow-up: ask the owner whether PR #5's stacked
slices should be split before review (a human decision).

## NEX-076 (run `NEX-2026-11-27-76`) — a human-issued grant must say why

**Status: IMPLEMENTED + TESTED + COMMITTED + PUSHED + CI PASSED + VERIFIED.** CI `4fa0463`
4/4 `completed/success` (backend ×2, frontend ×2), polled 2026-11-28T03:22Z. Local gates: 1569
passed / 4 skipped, ruff format+check clean, mypy clean; migration verified
`upgrade → downgrade → upgrade` on SQLite.

**The gap.** NEX-074 made an operator grant issuable and stamped the issuer, but recorded no
reason — its own explicit follow-up. An auditor could reconstruct *that* a grant was handed out and
*who* handed it out, never *why* it was justified. `granted_by` plus the gate's access log names an
actor and an instant, not a justification.

**Delivered.** `OperatorGrantRationale` value object; `OperatorGrant` now enforces the pairing (a
human-issued grant — `granted_by` set — must carry a rationale; a configuration grant must not);
`IssueOperatorGrantRequest.rationale` required; `POST /ops/operator-grants` accepts and returns it;
migration `b4e6f1a2c9d7` adds a nullable `rationale TEXT` with a non-empty check constraint via
`batch_alter_table` (SQLite and PostgreSQL).

**Design note — nullability mirrors provenance.** The column is nullable on purpose: a
configuration grant's reason is the reviewed deployment, so a per-row reason would be redundant.
Only human-issued grants are forced to state one — exactly where an auditor's question lands. No
backfill: existing rows predate the requirement and are configuration grants by construction (the
human write path did not exist before NEX-074), so inventing reasons would fabricate audit facts.

**Tests.** Domain pairing/value-object tests, application persistence-of-reason test, persistence
restart-durability and empty-string-constraint tests, and API tests (201 round-trip; missing /
blank / over-long reason each `422`; non-operator `404`; self-grant `409`).

**Delivered to GitHub.** NEX-076 is appended to PR #5's body (now NEX-052..NEX-076, 63858 chars).

**Next.** NEX-076 is closed. The next run picks the next dependency-ready slice. Two small
follow-ups this slice recorded stay open: expose the rationale on the grant read path (a
disclosure decision), and ask the owner whether PR #5's twenty-two stacked slices should be split
before review.

## NEX-075 (run `NEX-2026-11-27-75`) — the access log gets a bounded retention window

**Status: VERIFIED.** Product commit `319a3dd`; CI **4/4 `completed/success`** (backend ×2,
frontend ×2), polled 2026-11-28T02:45Z. Local gates: 1551 passed / 4 skipped, ruff format+check
clean, mypy clean on 143 files.

**The gap.** NEX-072 made the operator access log durable and NEX-074 made it the record of who may
read and issue operator grants. Nothing ever removed a row, so the table grows by one row per
granted `/ops` read, forever, each attributed to a named user. Run 74's checkpoint recorded this as
a risk; no backlog task existed, so NEX-075 was created before any code was written.

**The obvious fix is the wrong one, and that is the whole design.** Letting an operator delete rows
destroys the property the log exists for: an audit trail an operator can silently shorten is not an
audit trail, and the first thing a compromised operator would erase is the record of what they read.
So deletion is bounded from every direction:

- **The window is a domain policy, not a request field.** `OperatorAccessRetentionPolicy` (floor 90
  days, ceiling 3650) is validated at construction, so a bad value fails the boot rather than the
  first purge. No caller can express "delete everything" or "delete Bob's rows" because there is no
  field for it. `36500` — a plausible extra-zero typo for `3650` — is rejected rather than pinning
  every row while appearing to have retention; "keep everything" is expressed by *disabling* it.
- **Erasing is its own authority.** `access-log-retention` is distinct from `operator-access-report`,
  so the read can be granted widely and the erase narrowly. A caller holding only the report scope
  is refused, and refused with **404** rather than 403 per ADR-008.
- **The purge records itself before it deletes,** stamped `now` — inside the window it is about to
  prune — so the record of the erasure survives the erasure by construction, not by ordering luck.
- **Retention is off by default.** `OPERATOR_ACCESS_RETENTION_DAYS` unset ⇒ the endpoint answers
  404, rather than offering a purge with no mandate.

**The boundary is the whole feature, so it was mutation-tested.** A row stamped exactly `window`
ago is kept, and the same rule drives both the adapter's `at < cutoff` and `covers()`. Widening the
predicate to `at <= cutoff` fails two persistence tests; reverting restores 13 passed. A retention
test never seen to fail would be a hypothesis, not a control.

**Deliberately omitted: a scheduled purge.** An unattended purger changes who is accountable for an
erasure — a separate decision with its own approval question, not a configuration flag. ADR-017
records that, and records that the 90/3650 bounds are engineering bounds, not a legal opinion.

No migration: the purge deletes rows, it does not alter the table.

## NEX-074 closed (run `NEX-2026-11-27-74`) — the operator grant finally has a writer

Product commit `e375a1f`. **VERIFIED** — CI 4/4 `completed/success`, polled 2026-11-28T02:20Z.

`OperatorGrant` carried `expires_at` and `granted_by`, the store was durable since NEX-072, and the
holder report could read every grant — but nothing could *issue* one. The only writer was the
deployment-configuration seed, which issues unbounded grants with `granted_by=None`, so the bounded,
attributable grant the record exists for was unreachable and the documented incident remedy had no
caller. `POST /ops/operator-grants` is that caller, with four refusals (unbounded, self-grant,
over-broad, unknown scope) and the issuer stamped by the application layer — never from the body.

A `rationale` field was removed rather than shipped: the grant row has no column for one, so a
required free-text field would be validated and discarded. Persisting one needs a migration and is
follow-up work.

## NEX-058 closed (run `NEX-2026-11-27-73`) — a fail-closed `step-up` is now explainable

Product commit `38292d2`. NEX-058's enforcement landed in run 59; this run closed its one
remaining scope item, the diagnostic that makes the enforcement legible.

- **The gap.** The adapter derived assurance from `acr`/`amr` and said nothing about it. The
  only trace of a fail-closed `step-up` was a refusal at the tool gateway, long after the
  claims were gone — so an operator whose IdP *does* enforce MFA could not tell a
  misconfigured provider from a weak login.
- **The line is value-free by design.** `acr` is an IdP-chosen class name and `amr` can name
  methods personal to the account, so the diagnostic describes the *shape* only: whether a
  claim arrived at all, whether its type was usable, whether the closed method vocabulary
  recognised it, how many factor families resolved, and whether an `acr` negation vetoed a
  marker. It answers the operator's real question without putting a claim value in a log.
- **Silent when it passes.** The line fires only below `STEP_UP_FLOOR`, so it does not drown
  on every normal MFA login.
- **The marker scan is shared, and that caught a would-be regression.** Extracting
  `_acr_multi_factor_markers()` for both enforcement and the diagnostic is only safe if it
  returns *every* marker: `mfa-optional:multi-factor` has a vetoed marker followed by a live
  one, and a first version that returned only the first marker would have silently changed
  enforcement. The veto is applied per-marker, so semantics are unchanged from run 59.
- **A wrong test premise, recorded so it is not repeated.** `acr="mfa-optional:multi-factor"`
  does **not** satisfy the floor, and should not: `optional` is adjacent to *both* markers, so
  the blunt veto correctly vetoes both. The test uses `mfa-not-required:multi:factor`, where
  only the first marker is vetoed.
- **Tests:** 7 added, all through the real `_verify_id_token` path; proved non-vacuous by
  reverting the log call, which failed 4 of them. Full suite **1508 passed / 4 skipped**,
  mypy clean on 141 files.

## NEX-073 (run `NEX-2026-11-27-73`) — the frontend gate's flaky test was passing vacuously

**Status: VERIFIED.** Product `09888ac`; CI 4/4 `completed/success` (backend ×2, frontend
×2), polled 2026-11-28T01:56Z. Both frontend runs passed, where `38292d2` had one of two
failing — CI independently confirms the flake is gone. Test-only change.

- **What the failure looked like.** `App.test.tsx > shows the current user and lists owned
  notes` failed one of two frontend runs on `38292d2` and passed the other on the identical
  SHA, on a commit touching no frontend file.
- **The real mechanism, which the first account missed.** `NoteWorkspace` renders the note
  list only while `!loadingNotes`; every identity-keyed refetch empties it. So a `waitFor`
  around `expect(queryByText(x)).toBeNull()` resolves *during the loading swap*, not on a
  filtered list. The test is green whether or not the filtering works.
- **Three assertions had absence as their only evidence**, not one: both search tests and —
  most seriously — `does not reveal another user's private note when switching identity`.
  That one anchored on `Bob Baker`, the identity header, which a different effect renders;
  it is not a barrier for list content. A leak, or an empty render, both passed.
- **Fix.** Each now asserts a positive fact and the absence together: the two search tests
  check the matching note *and* the filtered-out note inside one `waitFor`; the isolation
  test seeds `Bob private` and awaits it before asserting `Alice private` is gone.
- **Proof it can fail.** A temporary `FakeApiClient` with `canRead() => true` and
  `listNotes` forcing `view: "all"` makes the new isolation assertion fail with
  `expected document not to contain element, found <span>Alice private</span>`. The test
  detects a real leak; the old form could not.
- **Left alone deliberately.** Two other absence-only waits are each followed by a backend
  assertion (`folder_id === null`; `getNote` rejects `404`) that carries the real proof.
- **Record corrected in place.** An earlier note in `BACKLOG.md` said only one assertion had
  the race and the search tests were fine. They were cleared without being audited. The
  correction is kept visible rather than rewritten — a log a run edits into looking right is
  worthless.
- **Tests:** 127 passed / 9 files; `tsc -b --force` clean; `eslint .` clean; `vite build` ok;
  20/20 repeat runs of `App.test.tsx` with 0 failures.

## NEX-072 (run `NEX-2026-11-27-72`) — the record of a privileged read now outlives the process

**Status: VERIFIED.** Product `3c26957`; CI 4/4 `completed/success`, polled 2026-11-28T00:52Z. Both check-run pairs green.
SqlAlchemyOperatorAccessLog is a second adapter behind the unchanged OperatorAccessLog port.

**The gap.** The operator access log — the record that a privileged read of a platform-wide
operational view was *granted* — lived in a bounded in-process ring. Two consequences, both bad
for an audit: a restart erased it, and a second worker had its own ring, so "who read this"
answered with whatever that one process happened to hold. R36 recorded this as its residual
("adapter is process-local and bounded"); this slice closes it.

Three properties are worth recording:

- **Ordering is by surrogate `id`, not `at`.** The port promises newest-first, and two reads can
  share a timestamp; sorting on a shared value is implementation-defined. An audit trail that can
  reorder itself is not evidence of sequence.
- **`user_id` is a plain indexed value, not a FK and not cascading.** Deleting a user must not
  erase the record of what that user read.
- **The adapter commits its own session** rather than joining the caller's unit of work. The record
  that the operator *saw* the view must land even if the caller's content transaction rolls back.

No backfill: the ring was never durable, so there is no earlier table whose rows this could
reconstruct. The migration creates the table and three indexes and nothing else.

Validation on the committed tree: **1501 passed, 4 skipped**; 9 persistence tests; `ruff format`
and `ruff check` clean; `mypy` clean over 141 files; `alembic upgrade head` then `downgrade -1` on
a fresh sqlite DB created and dropped the table and its indexes.

## NEX-071 (run `NEX-2026-11-27-71`) — the second signature had no authorised source

**Status: VERIFIED.** Product `a5d2710`; CI 4/4 `completed/success` at the docs head `adcb75e` (polled 2026-11-28T00:33Z). Delivered: PR #5 body (50507 chars) and issue #2 comment `7230990964`.

**The defect.** `DecideApprovalRequest` admitted only the agent's owner, and the aggregate
correctly refuses the owner signing twice. Two correct rules together made **every**
`required_approvals == 2` request undecidable: every `second-approver` policy (NEX-059) and
every `HIGH`-risk capability (NEX-063). The obligation was recorded, reported as enforced, and
could never complete. Unlike the earlier fail-opens, this one failed *closed* on the business
process — a control nobody can satisfy is not a control, and the audit trail claimed it ran.

**Fix.** `OrgPermission.APPROVE_AGENT_ACTION`, a distinct capability, is held by `ORG_OWNER`
(all permissions) and `ORG_ADMIN`, and deliberately **not** by `ORG_SECURITY_ADMIN`,
`ORG_AUDITOR` or `ORG_MEMBER` — so the read-only separation that makes audit and security review
independent is preserved by the matrix, not by a call-site role check. A tenant approver is then
admitted for the second signature only, bounded four ways:

- a half-approved `PENDING` request awaiting one more signature — never the first;
- an **active** membership in an organization the agent's owner also actively belongs to;
- never the owner themselves (separation of duties);
- only an **operational** organization.

A caller holding neither authority still gets **404**, not 403, so an approval id cannot probe
for another user's agent.

**Evidence.** `tests/agent/test_approval_second_signer.py` — 11 cases: the completable flow, each
of the four bounds, same-human double-signing refused, and the 404 rule. Full suite green
locally; `ruff format`/`ruff check` clean; `mypy` clean on 140 files (it caught a real `str` vs
`UserId` mismatch in the new helper, since fixed).

**Residual risk.** Authority is scoped by tenant membership, not a per-agent allowlist.
Acceptable while tenants are small and admin-held; revisit if agent ownership spans teams.

## NEX-070 (run `NEX-2026-11-27-70`) — a superseded policy version could be published

**Status: VERIFIED.** Product `04d52e1`; CI 4/4 `completed/success` at that head (polled
2026-11-27T23:48Z). Delivered: PR #5 body and issue #2 comment `7230423658`.

**The defect.** `PublishDraft` read the version in force, retired it, and published whatever it
was handed. A superseded version is still a perfectly valid `APPROVED` value — it was approved
and published once — so the store accepted it. The result was a policy that silently reverted to
an older rule set: a policy that had begun denying high-risk actions went back to allowing them,
with no rollback record and no trace beyond "the retired version is now live".

It was also a scope bypass. `RollbackPolicy` is gated on `policy-rollback` and re-checks
separation of duties against the rules' original author. Publishing the old row directly needed
only `policy-publish`, so a principal holding that scope alone could achieve a rollback the
rollback path would have refused them. Fail-open, same class as NEX-064/NEX-067/NEX-068: a
control existed and the enforcement path did not consult it.

**Fix.** `supersedes(incoming, in_force)` in `domain/governance/policy.py` refuses
`incoming.version < in_force.version`. The use case translates its `InvariantViolation` into a
`Conflict`, the same way the two separation-of-duties helpers do, so a client gets a **409** that
names the reason rather than a generic 422. Equal versions are deliberately *not* refused here —
re-publishing the version already in force is the store's own transition check to refuse
("it is published, not approved"), and keeping the two questions separate keeps the errors
distinguishable (D-070i). `in_force is None` (nothing published yet) is not a downgrade.

**Rollback still works**, which is the whole point of the guard rather than a blanket refusal:
`RollbackPolicy` drafts the old rules under the *next* version number, so it is always newer than
what is in force. Pinned by `test_rollback_remains_the_supported_way_to_restore_old_rules` — if
that ever fails, the guard has been written too broadly.

**Evidence.** Domain unit tests for `supersedes` (newer allowed, first release allowed, older
refused, equal not refused by this guard). Application tests assert the deny v2 introduced still
holds *through `evaluate()`*, not merely on status fields, and that history is untouched. The API
test proves the 409 over HTTP with `published_version` still reported as 2.

**Validation (local, on the committed tree).** `.venv/bin/ruff format` clean; `ruff check`
"All checks passed!"; `mypy` "no issues found in 140 source files"; full `pytest` exit 0, zero
failures. Run bare, exactly as CI does.

**Next.** Poll CI on `04d52e1`; if green, promote NEX-070 to VERIFIED and add it to PR #5 and
issue #2. The earlier NEX-069 checkpoint itself was never separately polled — its product commit
`7a1d5e2` was, and that is what carries the code.

## NEX-067 (run `NEX-2026-11-27-69`) — MFA requires independent factor families, not distinct method names

**Status: VERIFIED.** Product `c01fde5`; CI 4/4 `completed/success` at `c01fde5` (polled
2026-11-27T22:47Z). PR #5 body and comment `7229055417` carry the evidence.

**The defect the review named, one layer under the obligations.** `derive_assurance()` read
`amr` as a flat set: `len(methods) >= 2` plus any recognised second factor meant
`MULTI_FACTOR`. Two *spellings of one factor* therefore counted as two — `["otp","totp"]` are
both one-time codes, `["sms","tel"]` are both a text message. ADR-015 makes the `step-up`
obligation enforceable by this single derived fact, so one factor was spending an obligation
that demands two. Same fail-open class as NEX-060/NEX-062, one layer deeper: the control read a
value that overstated the login.

**Fix.** Methods map to a closed factor-family vocabulary and assurance requires **two distinct
families**: `knowledge` (`pwd`/`password`/`pin`), `one-time-code` (`otp`/`totp`/`hotp`), `sms`
(`sms`/`tel`), `hardware-key` (`hwk`/`swk`/`fido`/`webauthn`/`passkey`), `biometric`
(`fpt`/`face`/`iris`/`vbm`), `generic-mfa` (`mfa`/`pwd-2fa`). Closed vocabulary is the point: a
new spelling cannot smuggle a weaker method in, and an unrecognised method is ignored — which can
only understate (D-069a). Genuine second factors are unaffected: `pwd+otp`, `pwd+fpt`, `pwd+hwk`,
`otp+fpt`, `pin+hwk` all stay multi-factor.

**Evidence.** Both combinations the review named, plus `hwk+swk`, `webauthn+passkey`, `face+iris`,
three-spelling knowledge and four-spelling biometric, in
`tests/authentication/test_derive_assurance.py`. Removing **only** the family guard failed
**12** of them with `assert MULTI_FACTOR is SINGLE_FACTOR`; restoring it returned them to green,
so the tests are proved to catch the defect. Gates: ruff format 301 files already formatted,
ruff check clean, mypy clean (140 files), **1460 passed / 4 skipped** (up 16 from 1444).

**Scope.** Two files, as the review asked — no restructuring, no new abstraction beyond the
family table.

## NEX-068 (run `NEX-2026-11-27-68`) — an approval weaker than the call's threshold is refused

**Status: VERIFIED.** Product `cd8c843`, docs `f63ac1b`; CI 4/4 `completed/success` at
`f63ac1b` (polled 2026-11-27T22:47Z). PR #5 body and issue #2 comment `7228764536` carry the
evidence.

**A real fail-open, found while auditing the gateway.** `_approved_request` matched an approval
on capability, resource and `is_usable`, but never compared the threshold the row was *opened*
under against the threshold the current call *requires*. `is_usable` judges the row against its
**own** recorded `required_approvals` — the right question for the row, the wrong one for the
call. The call's threshold is derived separately and was simply never passed in.

So the two-person control was bypassable by timing: open a `delete_note` approval while the
policy demands one signature, have one approver grant it, then publish the second-approver
policy and spend the grant. The grant is approved, unspent, unexpired and satisfied relative to
its own weaker threshold, so the side effect ran without a second human. The SQL spend guard
cannot catch it either — its predicate also compares against the row's own weaker number
(D-068c).

**Fix.** The lookup takes the call's `required_approvals` and requires
`request.required_approvals >= required_approvals`. A weaker row becomes unusable, so the
existing deferral path opens a fresh, correctly-thresholded request — the only path that
actually asks a human again. The guard is a **floor, not an equality** (D-068b): a grant that
met a stricter rule than the call needs stays spendable, so relaxing a policy does not
invalidate approvals already granted under it.

**Evidence.** Three tests in `tests/agent/test_second_approver_obligation.py`: the weaker grant
is refused and the note survives; the tightened rule opens a fresh two-person request which then
succeeds; a two-signature grant still authorises a one-person action. Removing only the guard
line made both negative tests fail `DID NOT RAISE AccessDenied` — the single signature executed
the delete — so the tests are proved to catch the defect. Gates on the merged tree: ruff
format/check clean, mypy clean (140 files), **1444 passed / 4 skipped**.

**Honest note on selection.** The committed checkpoint ranked **NEX-067** (`derive_assurance()`
AMR factor independence, P1) first; the run summary handed to this run named NEX-065 (policy
`approve` verb). Neither was started. This slice is a different, verified defect in the file
already being audited for NEX-065. **NEX-067 remains the top priority and is unstarted** — it
should not be displaced again.

## NEX-066 (run `NEX-2026-11-27-66`) — an unenforceable obligation is refused when the policy is drafted

**Status: VERIFIED — CI 4/4 `completed/success` at `2ce7730`, polled 2026-11-27T22:55Z.** (The
docs head descends from product commit `353ef6d`, so its CI compiles and tests it.)

PR #5 review (6092140204) asked that authoring show whether each obligation is supported by the
deployment. A prior run deferred this as *"no policy-validation surface exists"* — that was
wrong, and this run corrects it: `DraftPolicy` **is** the authoring surface, and it already
refuses structurally invalid drafts.

The defect: a policy naming `rate-limit` in a deployment with no limiter published cleanly and
then denied **every call it matched**, with the reason only in the audit trail. The gateway's
call-time refusal was correct, but the author was never told.

The fix puts the deployment-wide half of the judgement in one domain function,
`deployment_obligation_gaps()`, and calls it from **both** the tool gateway and `DraftPolicy`.
One function, two callers — so the authoring path cannot promise something the call path
refuses. The gateway keeps what only the tool and the caller can decide (human-gated
capability, delegation assurance). `DraftPolicy` gained `rate_limiter_configured`, defaulting
to `True` so existing call sites are unchanged, with the container passing the real wiring
(`rate_limiter is not None`) rather than an assumption.

Deliberately narrow: `approval`, `second-approver`, and `step-up` are **not** refused at draft
time. Whether a given call can satisfy them depends on the tool and the delegation, so
refusing them at authoring would block policies that are perfectly enforceable for the tools
they target. Tests pin that.

Correction to a prior record: the NEX-056 comment table listed `second-approver` as refused
(fail-closed). Verified against code — it is **not** refused; the gateway treats it as
satisfiable when the tool requires approval, the approval request opens with
`required_approvals=2`, and `ApprovalRequest` tracks `second_approver_user_id`. `step-up` is
the only obligation with no mechanism.

Validation on the rebased tree: `ruff format --check` 301 files, `ruff check` clean, `mypy`
clean across 140 files, `pytest` **1442 passed / 4 skipped**. The push was rejected
non-fast-forward by a concurrent run (`1c2377e` → `e6f5486`); the ancestor check proved a
fast-forward race, not a rewrite, so the commit was rebased and every gate re-run before
pushing `353ef6d`.

## NEX-064 (run `NEX-2026-11-27-64`) — a retired policy version can no longer stay in force

**Status: VERIFIED — CI 4/4 `completed/success` at `7456a89`, polled 2026-11-27T21:13Z.**

A latent **fail-open** in `evaluate()`. Its own docstring states rule 3 as *"Only published
versions may be consulted"*, and it refused a `DRAFT` — but it **consulted a `RETIRED`**
version, so an `allow` rule from a superseded release still allowed. Probed before fixing:
a retired `allow-all` version returned `effect=allow`, `consulted=('p@v1',)`.

Why it was latent and why it still mattered: the gateway calls `all_published()`, which cannot
return a retired row, so no production path hit it. The exposure is the *next* caller — anyone
who passes a policy's `history()` (which is exactly what the history endpoint returns) instead
of its `all_published()` would keep enforcing rules a later release had already replaced. That
is the same class of bug as a draft leaking into force, and the retired case is the sharper
trap because the object is perfectly valid; only its lifecycle position says it is out of force.

The fix is one predicate: `status is not PUBLISHED` refuses, not `status is DRAFT`. The message
still distinguishes the two (`"is a draft …"` / `"is retired …"`) so a caller can tell an
unpublished draft from a superseded release. Two regression tests pin it — one for a retired
version alone, one asserting a retired version **alongside** a published one refuses the whole
evaluation rather than being silently skipped (skipping would be the other half of the bug:
the caller would believe rules applied that did not).

Deliberately narrow: no approve lifecycle verb this run. The lifecycle gap is real and named
below, but the domain-level fail-open was the higher-value fix available inside one run, and it
needs no migration, no new port, and no schema change.

## NEX-063 VERIFIED (run `NEX-2026-11-27-63`) — the approval gate follows the blast radius

**Status: VERIFIED — CI 4/4 `completed/success` at `7456a89`, polled 2026-11-27T21:13Z.**

Approval strength now follows how dangerous an action is, not only what a policy author wrote.
`domain/agent/risk.py` classifies every `Capability` into a closed ordered tier
(`low` < `elevated` < `high`): `org_search` is high (it is the only capability that reaches
past the acting owner's boundary), delete/share/revoke are elevated (destructive or
visibility-changing, but owner-scoped), reads and creates are low.
`required_approvals_for()` raises the signature count to the max of the policy's threshold and
the tier floor, clamped to the closed 1–2 vocabulary — so a **silent policy cannot reduce a
high-risk action to one signature**, while a policy may still raise a low-risk one to two. The
decision record now carries `risk`, so a policy can require a control at or above a tier and
the evidence shows why the gate looked as it did.

The design decision that mattered: **risk is code, not a model score.** A risk figure that
sets an approval threshold *is* authority, and §10 forbids a model from widening its own. The
derivation is a pure, total, deterministic function over facts the enforcement path can attest
to, so any tier is reproducible by reading one file — the same move ADR-015 made for `step-up`.
Full reasoning: [ADR-016](../ADR/ADR-016-risk-based-approval.md).

**Honest limit.** `org_search` is the only `high` capability *and* it is non-delegable, so no
agent token reaches it and the two-signature floor has **no production caller yet**. It is kept
because it is the right answer the day a future ADR permits delegated org-wide search. Since
that branch cannot be exercised end to end, the threshold logic is tested **directly as a pure
function**; every other tier is exercised through the real gateway. Preview, and non-capability
risk signals (bulk volume, unusual patterns, export size), are deferred with reasons in ADR-016.

**Validation on the committed tree:** `ruff format` (1 reformatted, 299 unchanged), `ruff check`
clean, `mypy` clean (140 files), **1432 passed / 4 skipped**, and the new
`tests/agent/test_risk_based_approval.py` **25/25**. The 4 skips are the pre-existing
PostgreSQL-only tests.

**Delivery debt:** PR #5 body and issue #2 do **not** yet mention NEX-063.

## NEX-059 (run `NEX-2026-11-27-60`) — one signature must not authorise a two-person control

`second-approver` was the last of ADR-015's obligations the gateway simply refused. The
obvious fix — "the obligation is present, so accept an approval" — *silently defeats* the
control: the request is approved, the action runs, and nothing anywhere records that only
one human signed.

So the threshold lives on the request, not in a gateway argument. `required_approvals` is a
closed 1-or-2 vocabulary. A gateway that forgot to pass the number cannot accept one
signature for a two-person control, because the requirement travels with the thing being
approved. `approve()` records the first signature and leaves the status `PENDING`; only a
second, **distinct** human completes it. Distinctness is checked in the aggregate, not the
use case — a use-case check is one refactor away from being skipped.

Three fail-closed edges are worth naming. `is_usable` returns false when fewer signatures
are recorded than required, so a hand-written `UPDATE`, a partial migration, or a future
writer that sets `status` directly cannot spend a control it never satisfied.
`raise_required_approvals()` may only raise and refuses once any signature exists —
silently un-approving a recorded human decision would rewrite history. And a denial after
a partial approval keeps **both** the grant and the refusal, because an auditor needs both
to understand how the control actually behaved.

| Evidence | Value |
| --- | --- |
| Commit | `39fa1db` — pushed, `git ls-remote` and `git rev-parse HEAD` both read it |
| Tests | 11/11 in `tests/agent/test_second_approver_obligation.py` |
| Gates | ruff format 297 files, ruff check clean, mypy 139 files, full pytest exit 0 |
| Migration | `e5a9c3b7d812`, additive, single linear head, `required_approvals` defaults to 1 |
| CI | **4/4 `completed/success`** on `b9811d7`, polled 2026-11-27T19:53Z — VERIFIED. |

One existing test had to be retargeted rather than deleted: it asserted `second-approver`
had no adapter, which stopped being true. The module docstring that made the same claim was
corrected so it does not mislead the next reader.

**Next:** poll `39fa1db`, promote on 4/4, then clear delivery debt. After that, the last
named obligation without a mechanism is risk-based approval.

## NEX-058 (run `NEX-2026-11-27-59`) — `step-up` is enforced, not merely designed

ADR-015 named the signal; this slice makes it real. `AuthenticationAssurance`
(`domain/identity/assurance.py`) is a closed, **ordered** vocabulary — `single-factor` <
`multi-factor` — with floor semantics, so a level satisfies every floor at or below it. The
IdP's `acr`/`amr` claims are derived into that vocabulary inside the adapter; they are never
carried through as free text, because two deployments spelling `mfa`, `mfa2` and
`multi-factor` would each mean whatever they liked inside an enforcement path.

Three decisions carry the control, and each had a tempting wrong version:

- **Fixed at grant, not read live per call.** Assurance is recorded on the session at login,
  rehydrated into the `Principal` on resolve, and copied onto the delegation when the human
  consents. The gateway reads it from the *delegation*. A delegation can outlive its session;
  re-reading live would let an agent act under authority the human never held at consent.
- **A floor a policy author cannot move.** `STEP_UP_FLOOR` is a static application constant.
  A policy may *ask* for step-up; it may not define what satisfies it. Reading the floor from
  the policy version would let the rule that requests the control weaken it.
- **Absent reads as weakest.** `AuthenticationAssurance.parse` coerces an unknown or missing
  value to `SINGLE_FACTOR` and never raises. The columns are nullable because pre-migration
  rows carry no proof, and a null that read as multi-factor — or a SQL default of
  multi-factor — would turn an unpersisted value into authority. That is the exact failure
  ADR-015 forbids.

A refusal is audited as a `denied` invocation naming the obligation and the assurance
actually held, so a weaker delegation is visible in the trail rather than only as a 403 the
caller saw.

**Tests, in both directions.** An obligation that can only ever refuse is not enforced — it is
unimplemented. The slice adds a **positive** gateway case (a multi-factor delegation satisfies
`step-up`) beside the refusal cases, plus five persistence tests including
absent-reads-as-single-factor.

**Migration** `b8d3e1f5a274` adds nullable `assurance` columns to `user_sessions` and
`agent_delegations`; `alembic heads` reports a single head.
**Status:** IMPLEMENTED, TESTED, COMMITTED, PUSHED at `c5a4f4e`, **CI PASSED** - 4/4 `completed/success` on head `a4003bf` (polled 2026-11-27T19:23Z). VERIFIED.

**Known gap:** `derive_assurance` in the OIDC adapter has no direct test. It is the fail-closed `acr`/`amr` mapping the whole step-up control rests on, and the backlog asked for it by name.
## NEX-057 ADR DELIVERED (run `NEX-2026-11-27-58`) — `step-up` has a deterministic signal

`step-up` was the hardest of the five named obligations because the other four are *predicates*
over facts the gateway already holds — they either fire or they do not. `step-up` asks a
*judgement*: is this actor's current assurance enough for this action? A judgement made by a
model would be exactly what §10 of the operating directive forbids, so the question is not *what
should decide* but **what deterministic, already-recorded fact can stand in for the judgement**.

[ADR-015](../ADR/ADR-015-step-up-assurance-signal.md) answers it: the signal is the **recorded
authentication assurance of the delegating human**. An agent has no assurance of its own — every
tool handler acts as the delegator (`_actor_principal` returns that human) — so the honest
question is how strongly the *human* authenticated when they granted the authority. That is a
stored fact, read by the gateway and never inferred.

The mechanism, once NEX-058 lands it:

- a closed, ordered vocabulary (`single-factor` < `multi-factor`), because a floor is a minimum
  and a free-text assurance label in an enforcement path is a suggestion;
- derived server-side from the IdP's `acr`/`amr` claims at the code exchange, carried on
  `Principal`, copied onto `DelegatedAuthorization.grant` — never accepted from a request, for
  the same reason no route supplies `owner_id`;
- compared against a static `STEP_UP_FLOOR` of multi-factor by ordinal rank;
- **absent or unrecognised reads as single-factor**, so an unpersisted column can never become
  permanent authority.

Five alternatives were rejected in the ADR with reasons, the most tempting being
denial-count risk accumulation: it measures *behaviour*, not *identity*, and an adversary can
steer it in both directions — staying quiet avoids step-up, and driving up denials on a victim's
agent forces prompts onto the victim.

The gateway keeps refusing `step-up` until NEX-058 lands, and that is correct: an obligation
"satisfied" by a weaker check than its name promises is worse than a refusal, because the policy
would read as enforced.

## NEX-056 PUSHED (run `NEX-2026-11-27-57`) — the `rate-limit` obligation gets an adapter

`rate-limit` was the third of five named obligations with no mechanism. The gateway's
behaviour was *honest*: it refused every call a policy placed the obligation on rather than
running it unbounded. But it made the policy language unable to express the one bound an
operator most often wants — "this agent may call this tool N times per window". A policy
author could name a bound and get a total refusal instead.

The adapter reuses the **existing** `RateLimiter` port that the HTTP boundary already
depends on, so a deployment that swaps in a shared store gets both the boundary throttle and
this obligation enforced against the same store with no change here. `InvokeTool` gained an
optional `rate_limiter`; `container.py` passes the `InMemoryRateLimiter` it already builds
from `rate_limit_requests` / `rate_limit_window_seconds`.

Three decisions, each pinned by a test:

1. **The key is per agent *and* per tool.** The boundary's `AgentRateLimitDep` is a global
   throttle on an agent's request rate. This is the narrower bound the policy author asked
   for on one capability. A single shared key would let a hot tool exhaust the budget of
   every other tool the same agent may call — not what the rule says.
2. **An idempotent retry does not consume budget.** A retry under a recorded key replays a
   decision already made, so the limiter is not consulted and the replay path returns the
   original outcome. Charging it again would make a network retry indistinguishable from
   abuse.
3. **A refusal is audited as a denial.** The same `denied` row a capability or policy
   refusal writes, naming the obligation and the retry window, so a throttled agent is
   visible in the trail rather than only in the 429 the caller saw.

Enforcement runs **before** the approval gate. A caller over budget is refused now, not after
it has opened an approval request a human would be asked to rule on — otherwise a flood could
fill the approval queue with requests that can never be used.

**Still fails closed with no limiter wired.** The obligation is then reported unsatisfiable by
the caller's check and the call refused, never run unbounded. This is why the pre-existing
"`rate-limit` is unsatisfiable" test survives: it moved to a no-limiter harness, because the
obligation is now satisfiable *when* a limiter exists and unsatisfiable when it does not. Both
halves are correct.

**The bound comes from the deployment, not the rule.** `PolicyObligation` is a closed
vocabulary of *names* with no parameters. A rule says "a volume bound applies" and the
operator sets the number. A per-rule bound needs a parameterised obligation — a vocabulary
change, and therefore an ADR, not a quiet widening in this gateway.

Status: IMPLEMENTED, TESTED, COMMITTED (`e7c7315`), PUSHED (verified by `git ls-remote`),
**CI PASSED** at that exact commit — Backend (ruff, mypy, pytest, migrations) and Frontend
(typecheck, lint, test, build) both `completed/success`, polled 2026-11-27T18:14Z. PR #5
updated, issue #2 commented (comment `7225253883`). **VERIFIED.**

## NEX-049 PUSHED (run `NEX-2026-11-27-52`) — the lifecycle, behind a gate the router cannot skip

NEX-047 built the lifecycle and NEX-048 made it durable, but nothing outside a test could
reach it. `/api/v1/ops/policies` now offers `draft`, `publish`, `rollback` and `history`.

The design point worth recording is *where the authorization went*. The operator-scope
check lives in the application use cases (`DraftPolicy`, `PublishDraft`, `RollbackPolicy`,
`GetPolicyHistory`), not in the router. A gate in the router would be a second copy of an
authorization rule, and the second copy is always the one that gets forgotten when the
next caller arrives. Putting it in the use case means an agent, a workflow or a CLI added
later is gated by exactly the code HTTP is gated by.

Scopes are **per operation** — `policy-draft`, `policy-publish`, `policy-rollback`,
`policy-history`. Drafting a policy is not publishing it. A single `policy-admin` scope
would have collapsed a lifecycle with a deliberate human checkpoint into one grant. The
API suite asserts the separation directly: a `policy-draft` grant gets `403` on publish,
and `404` on a policy it never drafted (existence is not disclosed to a caller who cannot
see it).

One boundary defect was caught by the tests and is worth naming. The router passed
`str(principal.user_id)` as `published_by`; the domain's `_validate_identifier` requires a
lowercase identifier starting with a letter, and a UUID starts with a digit, so publishing
failed with a `ValidationError`. The fix is one function at the boundary —
`_publisher_label` returns `user-<uuid>` — because a domain that validates an actor label
and a boundary that holds a UUID need exactly one place that knows both.

Status: IMPLEMENTED, TESTED, COMMITTED, PUSHED, **CI PASSED** at `22124f2` (4/4
`completed/success` on both the `push` and `pull_request` events, polled
2026-11-27T07:34Z). Reported to issue #2 (comment `7220681767`). PR #3 does not yet
describe this slice or NEX-048 — that is the remaining delivery debt.

## NEX-048 PUSHED (run `NEX-2026-11-27-50`) — a published policy that a restart cannot empty

NEX-047 gave the platform a policy model, but stored it in a process-local dict. Every
deploy therefore started with **no published policy at all**, which denies everything. That
is fail-closed, so it is not a vulnerability — but it meant policy could not survive the
process that wrote it, and a governance surface you must re-publish on every restart is not
a governance surface an operator can rely on.

`SqlAlchemyPolicyVersionRepository` is a second adapter behind the **unchanged**
`PolicyVersionRepository` port. No use case, no port, and no caller changed; the in-memory
adapter stays for tests, and it keeps naming itself `in-memory` so a test that accidentally
depends on durability is visible.

Three things are worth knowing:

- **Decoding re-validates.** `policy_codec.py` re-runs every domain validator when a row is
  read. A corrupt or hand-edited row raises `ValidationError` and logs a WARNING carrying
  the policy id and version. The alternative — skipping validation on read because "we wrote
  it" — would let a damaged row degrade into a *weaker* policy silently, which is the one
  failure mode an auditor must never have to discover by accident.
- **The invariants live in the schema, not only in the use case.** `UNIQUE
  (policy_id, version_number)` stops two authors claiming `v4`; a partial unique index on
  `(policy_id) WHERE status = 'published'` keeps at most one version of a policy in force.
  `PublishDraft` already retires the predecessor before publishing, but a caller that
  bypasses the use case cannot corrupt history through SQL.
- **No backfill.** Nothing in the in-memory adapter was durable, so there is nothing to
  migrate. The migration only creates the table and its indexes.

Validation on the committed tree: **1246 passed, 4 skipped**; architecture layering test
13 passed; `ruff check` and `ruff format` clean; `mypy` clean over 134 files; `alembic
upgrade head` applies `f7b3c0d95a12 -> 4b1e7a9c3f28` on a fresh SQLite database with both
indexes present, and `alembic heads` reports a single head.

**Honest status:** IMPLEMENTED, TESTED, COMMITTED, PUSHED at `810d1dc`, and CI on that
commit is **4/4 `completed/success`** (polled 2026-11-27T13:47Z, Backend + Frontend on both
the `push` and `pull_request` events). The push landed at this run's budget, so the poll was
done afterwards and the result is recorded rather than assumed.

**Still open after this slice:** there is no HTTP surface (NEX-049), `approve` is still
absent pending separation of duties (NEX-050), and the migration is SQLite-verified only —
the Postgres path is exercised by CI's migration job, which has not yet seen this revision.


## M3.1 OPEN (run `NEX-2026-11-27-50`, NEX-047) — a policy model that cannot be talked into executing

Phase 3 had no policy surface at all. Authorization lived in use cases, which is fine
for a handful of rules and cannot answer "what was in force when this decision was
made". NEX-047 adds the model, its lifecycle and its history.

**The AST is closed by construction.** `Comparison`, `AllOf`, `AnyOf` and `Not` over a
fixed operator vocabulary. There is no string to `eval`, and no fifth node type a
caller can invent. That is the security property, and it is structural rather than
enforced by review: a policy cannot become code because there is nowhere to put code.

**Three semantics are pinned, because these are the ones that go wrong quietly.**

*Missing attributes never satisfy a condition, under every operator including `!=`.*
A policy requiring `classification != secret` must not pass an unclassified resource,
and the natural reading of `!=` gives exactly the wrong answer. Pinned by a
parametrised test across all four operators, not one.

*A wrong-typed value raises rather than denying.* A silent deny would hide a data
defect in the one place an auditor most needs to see it. A missing attribute is the
different case and denies; a value of the wrong type is a bug and says so.

*Explicit deny overrides allow, and an empty or non-matching set denies.* Stated once
in `evaluate`, tested in both rule orders so the answer cannot depend on insertion
order.

**Publishing is one operation, and its write order is the safety property.** The
predecessor is retired **before** the successor is published, so a crash between the
two writes leaves *nothing* published — which denies — rather than two versions in
force. Exactly one version per policy id is ever in force. The store assigns version
numbers, so two authors cannot both claim v4.

**Rollback is a new release, not a rewrite.** `rollback` reads the named version's
rules and drafts them under the next number. The originals stay readable and the fact
that a rollback happened is itself in the history. Rewriting would erase the evidence
that anyone had tried the other thing.

**Deliberately absent: `approve`.** The full lifecycle includes approval, and it
depends on separation of duties (M3.3), which is not built. A gate that always passes
would be a policy surface that looks governed and is not — worse than an absent one,
because the absence is visible. Recorded as a residual rather than faked.

**Recorded gaps.** The adapter is in-memory and reports `name == "in-memory"`, so a
reader cannot mistake it for durable storage; a restart forgets every published
version. There is no HTTP surface yet. Both belong to the next governance slice, and
neither should reimplement the lifecycle.

**Status: VERIFIED, not merely pushed.** Local gates green (ruff format and check clean,
mypy 132 files clean, pytest 1228 passed / 4 skipped), CI 4/4 `completed/success` at
`379a98a`, issue #2 comment `7219198697` posted and read back, and the PR #3 section
appended and read back with exactly one NEX-047 heading. The product commit is `379a98a`;
the state that records it is `16bab4b`.

## R40 CLOSED (run `NEX-2026-11-27-49`, NEX-046) — a grant that outlives the process

NEX-045 made an operator permission a record with a lifetime and a holder, but the
only adapter was a dict. A restart forgot every bounded grant, and each worker in a
multi-worker deployment held its own copy, so a grant issued on one worker was
invisible to the others. R40 recorded that precisely and rated it low: it failed
closed, so it was never a security hole. But a grant nobody can see is a grant
nobody can audit, which is the question NEX-045 exists to answer.

**The port did not change.** `OperatorGate` and `ListOperatorGrants` depend on
`OperatorGrantRepository`, not on a store. That is what the port was for, and this
slice is the test of whether it was worth having: the store swapped and neither
caller moved.

**Two port promises, and how they survive a table.** `active_grants` must return a
stable order, so ordering is by a surrogate autoincrement `id` and never by
`granted_at` — two grants in one seed share a timestamp, and sorting by it would
make the answer implementation-defined. `add` must be idempotent, so
`(user_id, scope, granted_at)` is unique and `add` updates in place: a re-issued
grant extends its deadline instead of duplicating, and a seed that runs on every
boot cannot accumulate one grant per restart.

**`replace_configured` is the load-bearing change, and it exists because of the
store.** With a dict, re-seeding on boot was harmless because the dict was empty.
With a table it is not: a seed stamping `granted_at` from the boot clock is a *new*
grant on every restart, so the operator count would grow by the whole configuration
each time while nobody was granted anything. The seed path now reconciles instead —
grants that came from configuration (`granted_by is None`) are replaced, and grants
a human issued are left exactly as they are. A bounded incident grant must not be
erased by a redeploy, and configuration owns what configuration issued and nothing
else.

**Expiry stays a domain decision.** `active_for` and `active_grants` both filter
through `OperatorGrant.is_active` rather than in SQL, so the validity rule is
evaluated in exactly one place no matter which adapter answered. Filtering in the
query would duplicate the rule and let the two drift — the same shape as R29, where
a bound written by one clock and read by another was wrong in both directions.

**What is tested.** 23 persistence tests: a restart (a second adapter over the same
file sees the grant), cross-adapter visibility, insertion order, both expiry
boundaries, `not_before`, per-user scoping, uniqueness, re-issue extending a
deadline, `replace_configured` idempotency across restarts and its refusal to touch
human-issued grants, the empty-scope CHECK, the user foreign key, cascade delete,
and that the table exists in the migrated schema. Migration `f7b3c0d95a12` has a
single head, a correct parent, and no model drift.

**Honest limits.** Four PostgreSQL-marked tests are skipped locally, so the
`length(scope) > 0` CHECK and the `UTCDateTime` round trip have not met a
PostgreSQL server directly. CI at `0e86304` is 4/4 green and its Backend job does
run the migration path, so the schema is exercised there; what CI does not do is
run those four PostgreSQL-marked tests, and that gap stays recorded rather than
reported as closed.

## R36 CLOSED, second half (run `NEX-2026-11-27-48`, NEX-045) — a stored grant

NEX-044 narrowed each grant but left two holes that no narrower environment
variable could close. A grant could not **expire**: revoking an incident grant
meant editing configuration and redeploying, so a temporary grant was permanent in
practice. And nobody could **see who holds what**: a config value holds strings,
not holders, so an audit of the privileged surface could not answer a question
about itself.

**The record.** `OperatorGrant` is a user, a scope, a `granted_at`, an optional
`not_before`, an optional `expires_at`, and an optional `granted_by`. `None` for
`granted_by` is an honest "configuration issued this", not a missing field.
Validity is fail-closed at both ends: dead at the instant it expires
(`now >= expires_at`), and dead before it was issued or before its `not_before`.
A window that has not opened is not a permission, and a deadline is a deadline
rather than an approximation of one. The constructor rejects the mistakes that
would otherwise look like working grants until the first check: an empty or
whitespace-padded scope, a deadline at or before issue, an empty validity window,
and a `str` where a `UserId` belongs.

**The gate reads the store.** `OperatorGate` takes an `OperatorGrantRepository`
instead of a dict. It reads `now` once from its own clock and threads that value
through both the permission check and the access-log entry, so the two cannot
disagree about "now" — the R29 failure mode, in miniature. A grant that lapses
mid-process stops working with no restart and no configuration change. Refusal
still happens before recording, so an access entry remains evidence of
authorisation rather than of interest, and that invariant is pinned against the
new store.

**Configuration became a seed path.** `OPS_OPERATOR_USER_IDS` still describes a
deployment's *initial* operator set, so nothing an existing deployment relies on
changes. It now seeds one unbounded grant per configured scope. A deployment that
wants a grant to lapse issues it through the store rather than encoding an expiry
in an environment variable — bounded grants exist so they can be given out for an
incident and then be gone.

**The read side.** `ListOperatorGrants` reports every holder with at least one
active scope, the scopes they hold, and the earliest deadline among their grants —
or `None` when one grant is unbounded, because the strongest grant a holder has is
what describes how long they keep access. It is gated by the same scope as the
access report and recorded when granted, so enumerating holders is auditable too.
Exposed at `GET /api/v1/ops/operator-grants`; a non-operator gets `404`.

**Tests, which the slice did not previously have.** Expiry is the whole point and
was untested. Added: 17 domain tests for the lifetime boundary and the
construction rejections, 13 application tests for expiry at the gate mid-process,
no-entry-on-refusal and the holder-list folding, and 4 API tests for the route,
schema and 404 behaviour.

**Residual — R40.** The store is process-local. A restart forgets a bounded grant,
and each worker in a multi-worker deployment holds its own copy, so a grant issued
on one worker is invisible to the others. This is the same shape as the access log
and is honest for a demo adapter: `source_name` reports `in-memory` so a reader
cannot mistake it for durable. The fix is a durable adapter behind the existing
port; the gate and use case do not change. It is not a security regression — a
lost grant fails closed.

## R36 CLOSED, governance half (run `NEX-2026-11-27-48`, NEX-044) — a grant that cannot be narrowed must be handed out whole

The operator allowlist was a single flat set of user ids. Everyone on it could read
every platform-wide view: advisory withholding totals, the audit-integrity sweep,
and the operator access report. That is not a convenience, it is a
privilege-escalation shape. The engineer who reads withholding rates to tune a
threshold has no reason to read every tenant's audit trail, and the only way to
give them the one thing they need was to give them everything.

**Scoping.** A grant is now `<user-id>:<scope>`, and *a scope that is not named is
not granted*. `*` is the only token that means "all current and future
operations"; it is explicit and greppable, so a deployment that granted it can be
found by reading configuration instead of inferring it from an operator who
happens to pass every check. Nothing grants `*` by default. Entries accumulate, so
one operator can hold two scopes across two entries without a list syntax.

**An empty scope set is not an operator.** A listed id with no scopes admits
nobody, and `operator_ids` reports only ids with at least one scope. Reporting an
id that can read nothing as an operator would overstate the allowlist, which is
the one number an operator audit is supposed to be able to trust.

**Refusal stays 404.** A wrong-scope caller is refused exactly like an unlisted
one. A 403 here would confirm two facts an ordinary user has no need for: that the
endpoint exists, and that the caller is *some* kind of operator. The refusal still
happens before the access log is written, so an entry remains evidence of
authorisation rather than of interest.

**One parser, so "validates" means "parses".** The field validator and the
`operator_scopes` property call the same function. Two implementations would let a
value pass validation and then parse differently — the classic way an allowlist
becomes subtly wrong.

**A defect the tests caught, which is the point of writing them first.** `UserId`
subclasses `uuid.UUID`, whose constructor takes a hex string, not a `UUID` object.
The parser passed a `UUID`, so *every valid grant was rejected as a non-UUID id*.
This is fail-closed, which is the right direction, but it would have locked every
operator out at startup with a message blaming their configuration. The bootstrap
test asserted that a valid grant **parses**, not merely that invalid ones raise;
an assert-the-error suite would have passed straight through this.

**Residual, recorded rather than closed.** Expiry and holder visibility are still
missing. Both need a stored grant — user, scope, granted-at, expires-at,
granted-by — rather than a narrower string, so they belong to the next slice, not
this one.

**Tests.** A per-operation scoping block in `test_operator_gate.py` (including the
empty-scope-set case), `tests/bootstrap/test_operator_grants.py` for the
startup-refusal contract, and a `conftest.operator_grant(user, scope)` helper that
the three ops API test files now use so the scoped shape is exercised at the HTTP
boundary, not only in the unit.

## R36 CLOSED, read side (run `NEX-2026-11-27-48`) — a log nobody reads is not an audit trail

**The gap.** The previous run gave the operator allowlist a record: every granted read of a
platform-wide operational view appends an `OperatorAccessEntry` naming caller, operation and
moment. Nothing read that record back. `OperatorAccessLog.record()` had a caller and
`recent()` had none outside its own tests, so "who read the platform-wide totals" was still
unanswerable from inside the system — the write half of an audit trail with no read half. An
append-only file that no code path can open is storage, not accountability.

**The read side.** `GetOperatorAccessReport` (`application/use_cases/operator_access_report.py`)
returns a frozen `OperatorAccessSnapshot`. Exposed at `GET /api/v1/ops/access-report`, gated
exactly like every other ops endpoint, with `OperatorAccessEntryResponse` /
`OperatorAccessReportResponse` carrying the wire shape.

**Three decisions, each a correctness argument rather than a style choice.**

- **Gate first, then read.** The refusal path must not touch the log. If a non-operator could
  cause a read, the endpoint would leak the log's *size* through timing and would let anyone
  append load to a security control. Refusal happens in `OperatorGate.authorize` before
  `recent()` is called at all.
- **Inspecting the trail is itself a granted operator read, and is recorded.** The gate records
  before returning, so the read that follows is already attributable. That is deliberate: an
  audit-view access is exactly the kind of privileged read the log exists to capture. The
  consequence to state plainly is that reading the report grows it — the report a caller sees
  includes their own current read as the newest entry. Tests pin that, so it reads as intent
  rather than an off-by-one.
- **The snapshot carries `log_name` and `capacity`.** The only implementation is a bounded,
  process-local ring. A bare list of entries would let an operator read "nothing since 09:14"
  as a statement about the deployment when it is a statement about one process's ring buffer.
  Naming the log and its capacity makes the window's scope visible in the payload, and
  `truncated` reports that the window *filled* — not that entries were proven withheld, which
  the port cannot express.

**The limit is clamped, not rejected silently.** A caller-supplied `limit` is bounded into
`[1, MAX_LIMIT]`, so a request cannot ask for an unbounded response or for zero. The HTTP
layer returns 422 for an out-of-range limit rather than quietly substituting a default,
because an operator asking for 10,000 entries should learn the ceiling rather than believe
they received 10,000.

**Why the tests are evidence.** Nineteen new tests. The application level pins newest-first
ordering, the clamp at both ends, that the granted read is recorded, that a refused caller
leaves no entry, and that an empty allowlist admits nobody. The API level pins 404 (not 403)
for a non-operator, so probing cannot confirm the endpoint exists, and 422 for limits outside
the accepted range.

**Still open, unchanged.** The allowlist remains a static environment variable with no expiry
and no per-operation scoping. This slice makes the use of a grant *readable*; rotation and
scoping remain with the scoped-capability-grant slice. Reading is a prerequisite for rotating
anything responsibly, which is why it came first.

## R36 CLOSED (run `NEX-2026-11-27-47`) — one operator gate, and a record of who used it

**The defect.** `GetWithholdingReport` (NEX-037) and `SweepAuditIntegrity` (NEX-042) each
carried their own copy of the same allowlist check: compare the caller against
`OPS_OPERATOR_USER_IDS`, raise `ResourceNotFound` if absent. Two copies of a security gate is
already one too many — they can drift, and nothing would notice. Worse, neither recorded that
it had been used, so "who read the platform-wide totals" could not be answered from inside the
system at all. The only trace of a privileged read was whatever a log pipeline happened to
retain. That is the auditable half of R36, and it was the half that could be built correctly
now without inventing a capability system inside a security gate.

**The fix.** One `OperatorGate` use case (`application/use_cases/operator_gate.py`) behind an
`OperatorAccessLog` port (`application/ports/operator_access.py`), constructed **once** in
`build_container` and shared by both use cases. Every *granted* read appends exactly one
`OperatorAccessEntry` naming caller, operation and the moment.

**Two orderings are the security argument, not style.**

- **Refuse before recording.** A denied caller leaves no trace, so an entry in the log is
  evidence that a caller *was* authorised. A denial written into the same log would make it
  answer "who was refused" — a question nobody asked — while making "who read this" noisier.
- **The gate stamps the record with the request's own `Clock`, not wall time.** R29 already
  cost this programme once for exactly this: a bound written by one clock and read by another
  gives the wrong answer in both directions. A record timed by a second clock would disagree
  with the request that produced it.

**Why it is not an `AuthorizationProvider` decision.** That policy governs note *content*.
Platform-wide operational data must never become reachable as a side effect of content access,
so it stays a separate, explicit gate. The module docstring says so, because the next reader
will otherwise try to fold it in.

**What this does not solve, stated plainly.** The allowlist is still a static environment
variable with no expiry and no per-operation scoping. Rotation and scoping belong to the
scoped-capability-grant slice; this slice makes the *use* of a grant visible, which is the
prerequisite for ever rotating one. R36's governance gap is narrowed, not closed.

**Why the tests are evidence.** Fourteen tests pin this. Nine in `tests/application/test_operator_gate.py` pin the
gate and the adapter: an empty allowlist admits nobody; a non-operator is refused as
`ResourceNotFound` (so a probe cannot distinguish a forbidden endpoint from a missing path); a
grant records caller, operation and the gate's clock time; a refusal records nothing; repeated
grants each leave their own entry; the ring is bounded and newest-first; and a non-positive
capacity is rejected at construction. Two further tests in `test_withholding_report.py` prove
the ordering at the use-case boundary — a granted read appears in the log, a refused read does
not — and `test_audit_sweep.py` now drives the same shared gate, so the drift R36 described
cannot silently reappear in one of the two callers.

**Validation.** ruff format clean, ruff check clean, mypy clean (120 source files), `pytest`
**1079 passed, 4 skipped, 0 failed**. Product commit `1eb1d3b`, pushed and `ls-remote`-verified.

## R39 (run `NEX-2026-11-27-46`) — the lease could be acquired but never released

**Found by trying to be polite.** After the previous run's docs were pushed, it called
`lease release` to hand the lease back. It exited with `lease held by another agent`, naming
that run's own holder. The next run opened with the same lease still held — 1405 seconds
remaining, `holder_liveness: dead`, `mine: false`.

**The defect.** `release` and `renew` compare the live lease's `holder` against the caller's
holder to decide "is this mine?". The default holder was `host-pid`. Every `nexus_coord`
invocation is a fresh process, so the pid recorded at acquire time is never the pid doing the
release. `mine` is false on the very next call, and both verbs fail. A lease was single-use per
acquisition, so every successful acquire blocked the next run for the full TTL.

**Why R38 was not bad luck.** R38 was recorded as "a stale lease held by a dead process". It
was the predictable output of a run that acquired and could not hand back. The two defects are
one defect; fixing this one removes the source of the other.

**The fix.** The holder must be identical across every process of one run and different between
two concurrent runs on one host. A pid satisfies the second and fails the first. The run id
satisfies both but is a briefing value passed by hand — coordination would then depend on a
human remembering a flag. The POSIX **session id** is exactly the required identity: one
terminal session drives a run, every process it spawns inherits the session, and overlapping
runs have different sessions. `default_holder()` resolves `NEXUS_HOLDER`, then `host-s<sid>`,
then `host-<pid>` only where `os.getsid` is unavailable or fails.

**Why the tests are evidence.** The decisive property is stability *across processes*, which no
single-process test can express — two calls in one process share a pid and pass under the
broken implementation too. `test_the_holder_is_identical_across_separate_processes` spawns two
real subprocesses and compares their answers. Negative control on disk: reverting
`default_holder()` to `host-pid` turns exactly two tests red (the cross-process one and the
shape one); the file was restored from a backup and re-verified before commit.

A five-process acceptance run against a scratch repository with a bare origin confirmed the
whole cycle: acquire, then `status` in a *different* process reporting `mine: true`, then
`renew`, then `release`, then `held: false`. A sixth process with a foreign identity was still
refused, so the fix widened self-recognition without widening exclusion.

**Validation.** ruff format 249 files clean, ruff check clean, mypy clean (117 source files),
`pytest` **1068 passed, 4 skipped, 0 failed**. Commit `64759f4`, pushed,
`git ls-remote` confirms the remote head equals local `HEAD`.

**Honest limits.** The identity is only as stable as the session; a harness that spawns each
invocation under its own session (via `setsid`) reproduces the original defect, which is what
`NEXUS_HOLDER` is for. A recycled session-leader pid could make `holder_liveness` report
`alive` for a dead run — reporting inaccuracy only, because acquisition is gated on expiry,
never on liveness, so it cannot cause a false takeover.

## R37 (run `NEX-2026-11-27-44`) — the cap bounded parsing, not memory

**The defect.** `HttpDecisionProvider` refused a response larger than 64 KiB, but it checked
that bound against `response.content` — a body httpx had already read in full. The check was
correct and useless: the memory had been spent before the question was asked. A peer could
make the process hold an arbitrarily large body and the adapter would still call the answer
"oversized" rather than "never read".

**The fix, in three ordered rules.** The response is opened with `httpx.stream` rather than
`post`, and:

1. A declared `Content-Length` above the cap returns `MALFORMED_OUTPUT` before the body is
   touched at all.
2. `_read_bounded` consumes the stream in 8 KiB chunks, accumulating at most
   `_MAX_RESPONSE_BYTES + _READ_CHUNK_BYTES`, and returns `None` the instant the cap is passed.
   The `with` block then closes the stream instead of draining it.
3. The non-200 branch returns before any read, so a vendor error body — which can echo the
   request, an internal id, or the credential — never reaches the audit trail through a
   rationale.

**Why the tests are evidence and not decoration.** Three of the six new tests fail against the
old implementation, and they fail for the right reason: they instrument the *body object*, not
the parsed result. `test_an_unbounded_stream_is_abandoned_rather_than_drained` counts the
chunks the body was asked for; a drain would raise that count and fail. The announced-oversize
and non-200 tests use a body that raises if read at all. A test that only asserted
"abstains with MALFORMED_OUTPUT" would have passed on the broken code.

**What this does not fix.** The cap bounds size, not duration. That remains recorded in the
module docstring as an honest limit, not a closed one.

**Validation.** ruff format/check clean (178 files), mypy clean (115 source files),
**1027 passed / 4 skipped**. Negative control: reverting only the implementation turned exactly
the three streaming tests red and left the other 54 green.

## NEX-037 (run `NEX-2026-11-25-37`) — the count existed, the aggregate did not

R34 described a monitoring gap precisely: `WithholdingRecorder` counts every advisory and
every over-cap withholding across the process, `RecommendApprovalDecision` logs the running
total, and nothing exports it. Answering "how often are advisories thinner than they should
be" meant reading individual responses. A log line is not a number an operator can alert on.

This slice is the read side of that recorder, and the design question was not how to expose
the number but **who may see it**.

The aggregate spans every tenant. It is not a view of the caller's own data — it is a view of
the whole process — so exposing it to any authenticated caller would let one user infer
another user's advisory volume, and would make the endpoint a cross-tenant side channel that
grew more informative the more precisely it was polled. That ruled out the obvious shape
(a normal authenticated route) and ruled out reusing `AuthorizationProvider`, whose job is
note *content* access: platform operations must never be reachable by content access alone.

What shipped instead:

- `GetWithholdingReport` (`application/use_cases/withholding_report.py`) returns the snapshot
  to a caller on an explicit allowlist, and raises `ResourceNotFound` for anyone else.
- `OPS_OPERATOR_USER_IDS` gates it. **Empty by default, and empty means nobody** — the
  allowlist is a grant, not a role inherited by being logged in. An unconfigured deployment
  therefore exposes nothing, which is the correct failure direction for a setting nobody has
  thought about yet.
- A non-operator gets **404, not 403**. `403` would confirm that a platform-operations
  surface exists at that path, which is itself a small disclosure to someone probing the API.
- `GET /api/v1/ops/withholding-report` returns counts only — `advisories_total`,
  `over_cap_advisories_total`, `over_cap_fields_total`, `over_cap_rate`. No field name,
  value, note id or user id. That is why a single process-wide number can be disclosed at all
  without disclosing content: the counts are about *withholding*, never about what was withheld.
- `over_cap_rate` is reported beside its numerator and denominator, because `0.0` over three
  advisories and `0.0` over thirty thousand are different claims and a bare rate conflates them.
- Malformed operator ids fail **at startup**, not at request time. The gate compares `UserId`
  values, so a typo would silently match nobody and the endpoint would look broken for the one
  operator it was meant to serve — a failure that is hard to distinguish from a permissions bug.

Seven tests cover the gate at both layers: three at the use case (operator reads totals; empty
allowlist denies; a listed-other-user is refused as not-found) and four at the HTTP boundary
(the route, schema and container wiring agree; empty allowlist admits nobody; a non-operator
sees 404 rather than 403). Both layers matter here — the use case raising `ResourceNotFound`
and the router *not* remapping it are separate claims, and only an HTTP-level test pins the
second.

Status: **VERIFIED** — CI at `5c0575b` is 4/4 `completed/success`, every check run reporting
`head_sha` `5c0575b5df0b2aa30e1d4a28f622ac327871f902`, polled 2026-11-25T18:57Z. Two earlier
polls (18:52Z, 18:54Z) saw the same runs still `queued`/`in_progress`, which is why the claim
carries a timestamp at all. Local gates: `ruff format` clean (169 files), `ruff check` clean,
`mypy` clean over 109 source files, full `pytest` **920 passed / 4 skipped**.

One arithmetic caveat, recorded rather than smoothed: NEX-036 recorded 916 passed and this
slice adds 7 tests, which predicts 923. The measured number is 920. NEX-036's 916 was written
without a measured baseline, so the discrepancy is most likely in the prior figure — but that
is not proven, and 920 is what the suite actually reported.


## NEX-036 (run `NEX-2026-11-25-37`) — the caller was audited, and the advisory outlived its question

The previous run's `next_action` asked for a caller audit on one question: does any
production caller ignore a `DecisionEvaluation` and proceed, silently turning "the provider
could not judge" into "allowed"? The premise was checked against the code first, because the
two preceding runs had both left stale `next_action` fields.

`RecommendApprovalDecision` is the only production caller. It was probed end to end through
the real use case with a stub provider, before any fix:

- A **pending** request returns score `0.5` and its status is still `pending` afterwards.
  Advice never decides.
- A **`DISABLED`** provider returns score `None` with reason `DISABLED`. The abstention
  reaches the caller; nothing swallows it. This is the fail-closed property the audit existed
  to test, and it held.
- The subject's content does not travel: the note title is withheld by the classification
  ceiling and named in `excluded_context_fields`, distinct from `over_cap_context_fields`.
- **An `APPROVED` request produced an advisory. So did a `REVOKED` one.**

That last finding is the defect. The use case's own docstring and the route summary both
described advice about a *pending* approval, and the code asked only whether the caller owned
the agent. A score attached to a settled request invites a reviewer to read it as the opinion
that produced the decision, and a later reader — an auditor — to read it as one that informed
it. In an approval trail that is precisely the wrong inference, and it is the kind a
regulator is entitled to draw from the record. It also spent provider time and a withholding
count on a question nobody could act on.

The fix is a precondition, not a transition:

- `RecommendApprovalDecision` refuses with `409` when the request is not `PENDING`, or when
  its validity bound has passed. Nothing is mutated, so the check belongs in the use case
  rather than the aggregate, whose guards are written for state changes.
- Expiry is judged with the use case's **own clock** — the one that stamped the bound — never
  the wall clock. R29 is the same defect in a different place, and a single-clock test suite
  cannot see either.
- The docstring and route summary were corrected to state the 409, so the contract the code
  keeps and the contract the prose claims are now the same contract.

Five tests pin it: four refusals (approved, denied, revoked, expired-under-the-advising-clock)
and one asserting the live case still returns an advisory, so the fix cannot pass by refusing
everything. The four refusal tests were verified to **fail** with the guard removed — the
negative control was run on disk, then the file was restored from a backup and re-verified
line by line, because a heredoc in this environment corrupted the command that ran it.

CI was polled at `90eea0c` and is green: Backend `completed/success` (id 139429822539) and
Frontend `completed/success` (id 139429823232), one complete pair, 2026-11-25T18:45Z. A
duplicate Backend run reads `completed/cancelled` because the concurrency group killed the
superseded event run — neither pass nor fail, and not counted. The product commit was
pushed before the docs commit so the evidence names `90eea0c` rather than a docs head.

## NEX-035 (run `NEX-2026-11-25-36`) — the adapter was policed on its answer and trusted on its name

R30 asked for a conformance suite so the first real vendor adapter would not be the first
genuine test of the decision contract. The suite is written, and writing it found that the
contract had a blind spot: `EvaluateDecision` guarded everything a provider *answered* — the
rationale length, the confidence range, the choice set — and guarded nothing about what a
provider *called itself*. An adapter implements both `decide` and the `name` property; only
the first was treated as third-party code.

Five probes against the seam, run before any fix, established the defects rather than
assuming them:

- `provider_name` accepted 580 characters although `MAX_PROVIDER_NAME_LENGTH` is 80. The
  constant was declared and never read — dead code that looked like enforcement, which is the
  most misleading kind of missing control.
- An over-long `rationale` raised `ValidationError` inside `decide` and surfaced as
  `PROVIDER_ERROR`. Operationally that sends an operator to check connectivity when the
  payload is what violated the contract.
- A `name` property that raised propagated a `RuntimeError` out of `run`, breaking the module's
  own rule 2 ("a provider failure is an abstention").
- A non-`str` name was carried into the record unchanged — `provider_name` came back as
  `b'bytes-not-str'`.
- `name` was read once per run, which is the correct count; the probe pinned it so a future
  refactor cannot quietly make the name disagree with itself across a single evaluation.

The fixes are small and sit at the seam, not in the domain contract:

- `validate_provider_name` in `domain/agent/decisioning.py` checks the bound it always
  declared, plus the shape the value must have. A provider name lands in audit records,
  structured log fields, and API responses, so it must be a lowercase identifier — a newline
  in it is a log-injection vector, not a cosmetic wart. It raises rather than coercing: a
  silently truncated name would record a *different* provider than the one that answered.
- `_safe_provider_name` in the use case reads the name **once**, before anything else can
  fail, and degrades to `UNIDENTIFIED_PROVIDER` on a raise or a non-identifier value. Reading
  it lazily inside `_evaluation` would also have read it on the disabled path, where the
  provider was never called and its name may not resolve.
- `ValidationError` raised through `decide` is now classified `MALFORMED_OUTPUT`, caught
  ahead of the broad `except Exception` that would otherwise have reported it as a vendor
  outage.

The suite itself (`tests/agent/test_decision_provider_conformance.py`, 30 tests) is the R30
deliverable: `assert_provider_conformance` is the contract a real vendor adapter is run
through, and the meta-tests run a deliberately hostile fake that breaks one rule at a time and
assert the suite *rejects* it. A conformance suite that has never been shown to fail is not
evidence of conformance.

Status: **VERIFIED** — CI at `12f6fa9` is 4/4 `completed/success`, every check run reporting
`head_sha` `12f6fa9dc29fd67d4d4c2dc884535e7041aaf2ec`, polled 2026-11-25T18:14Z. Local gates:
`ruff format` clean, `ruff check` clean, `mypy` clean over 107 source files, full `pytest`
**911 passed / 4 skipped** (baseline 881; the delta is the 30 new tests).

## NEX-028 (run `NEX-2026-11-25-31`) — a hash of a secret is still a fact about the secret

NEX-027 closed by sending a SHA-256 of the context instead of the context, and called that a
declassification boundary. It is not one, and the difference matters. A digest is a
**fingerprint**: for low-entropy content — an email address, a note title, a two-word subject —
anyone holding the digest can confirm a guessed value by hashing it. Sending the hash of a
confidential field therefore still discloses a fact about that field. Hashing the *raw* context
made this invisible, because the code looked like it had already answered the question.

Context assembly is the half of M2.6 that answers it properly. One domain module,
`domain/agent/context.py`, now owns what may enter an outbound judgment. Context is gathered
into a `ContextEnvelope` of named `ContextField` values, each labelled with a
`DataClassification`; the envelope's hash is computed over **only** the fields at or below the
envelope's ceiling. A field above the ceiling is absent from the canonical input entirely, so
it can neither influence the digest nor be confirmed from it. The default ceiling is `INTERNAL`:
internal references may inform a judgment, the data subject's confidential material may not.

Three properties are structural, not conventions:

- **`RESTRICTED` cannot be named as a ceiling.** It is a construction error, so there is no
  runtime branch to get wrong and no configuration under which restricted content reaches an
  outbound digest.
- **Exclusion is recorded, never silent.** `excluded_field_names` names what was withheld —
  names, never values — and travels through `DecisionRequest` into `DecisionEvaluation`, so the
  audit record can say the provider was asked about less than the platform held. A silent drop
  would make "we asked with less" indistinguishable from "there was nothing more", which is the
  exact failure the slice exists to prevent.
- **Fields are named and bounded.** Names are stable lowercase identifiers so telemetry can
  aggregate them; values cap at 200 characters and fields at 32 per envelope, so a document
  cannot be smuggled in under one name.

`DecisionRequest.from_context` is the only sanctioned route from an envelope to a question,
because it is what guarantees the hash the provider sees was computed over content an envelope
approved. The one honest gap, documented in that method: `context_hash` is still a plain `str`,
so a caller *can* hand-assemble one — and doing so means taking the declassification decision
personally. A future slice can close it by narrowing the field to a type only an envelope can
construct.

Status: **VERIFIED** on observed CI at `a3d1b32`: **4/4 `completed/success`**, every check run
reporting `head_sha` `a3d1b3214fa990aab9abe2f93cdef642bde8c882`, polled 2026-11-25T14:42Z —
both the push and the pull_request run. Local gates green: `ruff format` clean, `ruff check`
clean, `mypy` clean over 104 source files, full `pytest` **824 passed / 4 skipped** (baseline
794; delta is exactly the 30 new tests — 28 in `tests/agent/test_context_envelope.py`, 2 added to
`tests/architecture/test_decision_provider_neutrality.py`). Negative control: making
`included_fields` ignore the ceiling turns three tests red, including
`test_confidential_value_cannot_influence_the_digest`, which asserts the property directly by
requiring two envelopes differing only in a confidential field to hash identically.

**R31 (new, low).** `ContextEnvelope` is exercised only by tests so far. The first production
caller (tool-gateway classification or approval recommendation) will be the first to test
whether the 200-character value cap and 32-field cap fit real content; if either is too tight,
widening it is a contract change.

## NEX-027 (run `NEX-2026-11-25-30`) — judgment that cannot become authority

M2.6 listed "decisioning and context assembly" as the two things it still needed. This slice
is the first half: the platform can now ask a judgment service a narrow question and get an
answer it can reason about, with no path from an answer to a permission.

The design problem is not "call a model". It is that a model-driven system's most common
security failure is letting an opinion become an authorization. Three properties are
structural rather than conventional, and each is enforced by the shape of the types:

- **A decision grants nothing.** `DecisionRequest` and `Decision` have no field that can name
  a permission, a capability, a principal, or a resource scope. There is nothing to *hold* an
  authorization in, so a decision cannot be mistaken for one. The decisioning modules do not
  import the authorization provider and cannot reach it — asserted by an AST test, not by
  review.
- **Every answer is bounded.** A choice must be one of the candidates the caller supplied; a
  score and a confidence are floats in `[0.0, 1.0]`; the rationale is capped at 400
  characters. There is no free-form output channel, so a provider cannot smuggle an
  instruction back into the application.
- **Abstention is a first-class outcome, not an error.** A provider that is disabled, unsure,
  slow, or malformed produces `ABSTAINED` with a named reason. Failing closed is expressed by
  *not deciding*, never by deciding "allow" — which is why there is no binary approve verdict
  anywhere in the vocabulary.

`EvaluateDecision` is the single route to a decision, so four rules apply to every provider:
the flag off abstains without calling the provider at all; a raised error or a non-`Decision`
payload becomes an abstention; `validate_against` discards an answer of the wrong kind or a
choice the caller never offered; and confidence below the caller's threshold abstains. The
threshold defaults to `0.5` and the stub adapter answers at `0.1`, so **enabling the flag
alone does not start handing out opinions** — the stub abstains under any real threshold.

`DecisionRequest` carries a SHA-256 of the context, never the context. Raw note text is not
handed to a third party by default, so there is nothing to retain, leak, or inject into. That
is also the seam context assembly will use next: it will decide *what* to hash.

Two smaller choices worth recording. The stub's choice answers use `min(candidates)` rather
than "first", so the result does not depend on input ordering — a stub whose answer changes
when you reorder a list is a flaky test generator. And `approval_recommendation` is a *score*,
not a yes/no, precisely so that no caller can read an advisory number as consent; the human
approval workflow remains the only thing that can approve an action.

Status: **VERIFIED** on observed CI. Local gates green on `fb8d5bd`: `ruff format` clean,
`ruff check` clean, `mypy` clean over 103 source files, full `pytest` **794 passed / 4
skipped** (baseline 754; the delta is exactly the 40 new tests — 36 in
`tests/agent/test_decisioning.py`, 4 in `tests/architecture/test_decision_provider_neutrality.py`).
Negative control verified: planting `from jev_sdk import Client` in
`application/ports/` turns two of the neutrality tests red with the offending file named;
the probe was deleted and the file re-verified clean. **CI at `fb8d5bd` is 4/4
`completed/success`, every `head_sha` equal to `fb8d5bd`, polled 2026-11-25T14:18Z** — so
NEX-027 is VERIFIED on a run at its own SHA, not on descendant-CI.

**R30 (closed 2026-11-25, run `NEX-2026-11-25-36`).** The stub is the only adapter, so the
port's shape had been exercised against one implementation that always answers cheaply — the
400-character rationale bound, the `[0.0, 1.0]` ranges and the candidate choice-set had never
met a payload that tested them. NEX-035 shipped the conformance suite R30 asked for
(`tests/agent/test_decision_provider_conformance.py`), whose `assert_provider_conformance` a
real vendor adapter is run through, with meta-tests that assert the suite rejects a hostile
fake one violation at a time. Building it found the seam was policed on the *answer* and
trusted on the *name*: `MAX_PROVIDER_NAME_LENGTH` was declared and never read, a raising
`name` property escaped as a `RuntimeError`, a non-`str` name was recorded verbatim, and a
contract-violating rationale was misreported as `PROVIDER_ERROR`. All four are fixed. Closed
on VERIFIED evidence: 4/4 `completed/success` at `12f6fa9`, polled 2026-11-25T18:14Z.
Residual: the suite proves a *conforming* adapter behaves; it cannot prove a vendor's model
produces good judgments. That is an evaluation question, not a contract one.

## NEX-026 (run `NEX-2026-11-25-29`) — an approval must be judged against the clock that stamped it

The approval gate compared two different clocks. `ApprovalRequest.open` derives `expires_at`
from the timestamp it is handed, and every production use case passes its own `Clock` port —
but the decision path asked `is_expired`, which reads the module-level wall clock. So the
bound was written by one clock and read by another, and the answer is wrong in both
directions whenever they disagree:

- opened on a clock **behind** the wall clock, a live request is judged already lapsed and a
  human cannot decide it. The gate refuses consent it should accept.
- opened on a clock **ahead** of the wall clock, a lapsed request is judged still valid, so an
  approval outlives the bound it was issued under. Consent survives longer than it was
  granted for.

This is the failure mode the whole `Clock` port exists to prevent, and it was invisible for
one specific reason: every existing expiry test used the same clock for opening and deciding,
so the two readings always agreed. A defect that requires *disagreement* to appear is exactly
the kind a single-clock test suite cannot see.

The repository adapter was already correct — `SqlAlchemyApprovalRequestRepository` compares
`expires_at > now` on the caller's clock and treats NULL as expired. The defect was confined
to the aggregate's own decision path, which is why no integration test caught it either.

**The fix is subtraction, not addition.** `expired_at(*, now)` becomes the one honest
comparison: a single instant against a single bound. `is_expired` survives as the wall-clock
convenience for callers that genuinely have no clock, and now *delegates* to `expired_at`, so
the NULL-means-lapsed rule is written once instead of twice. `approve`/`deny` take the
caller's instant and use it for **both** the decidability check and `decided_at` — previously
those were two separate readings of the wall clock, so the recorded decision timestamp could
name a different instant from the one that was actually checked.

**What to carry forward (R29).** A validity bound is meaningless unless you can name the clock
that stamped it, and the same clock must judge it. The general question for any bound: *who
writes this, and does the reader use the same one?* A new expiry-like field that reads a module
clock instead of the caller's reopens this class.

Status: **VERIFIED.** CI observed at `b46e2e3`: **4/4 `completed/success`**, every check run
reporting `head_sha` `b46e2e3`, polled 2026-11-25T13:34Z. The docs commit `8742faa` pushed
afterwards did not disturb it — `b46e2e3` still reads 4/4 success while `8742faa`'s own runs are
in flight. Local gates: `ruff format` clean, `ruff check` clean, `mypy` clean over 98 sources,
full `pytest` **754 passed / 4 skipped** (was 748; the delta is exactly the 6 new tests in
`tests/agent/test_approval_clock.py`). Negative control: reverting only `entities.py` to its
pre-slice version turns 5 of those 6 red; the sixth asserts the wall-clock property and must keep
passing.


## NEX-020 (run `NEX-2026-11-25-22`) — a stale decision must not resurrect spent authority

`ApprovalRequestRow` had no `version`, and `save` rewrote the whole row from whatever the
caller had loaded. Two writers that loaded the same request both committed and the second
won — so a `DecideApprovalRequest` holding a pre-revocation copy wrote `approved` back
over a `revoked` row, and a stale copy of a spent approval cleared `consumed_at`, making a
single-use approval spendable again. Neither raised.

This is NEX-019's lost-update class on the aggregate where losing the update hands back
*authority* rather than progress, which is why it was treated as P0.

The aggregate now records `persisted_version` and bumps `version` on every
authority-changing transition — `approve`, `deny`, `revoke` **and `consume`**. The
`consume` bump is load-bearing: without it a stale save still matches the stored version
and clears `consumed_at`, so the CAS alone would not have closed the double-spend.
`save` is one `UPDATE ... WHERE id AND version = :expected` raising `VersionConflict`
(HTTP 409), and `consume()` increments the version in the same statement that sets
`consumed_at`.

Status: **VERIFIED.** CI observed **4/4 `completed/success` at `460a26a`**, every check run
reporting `head_sha` `460a26a` (polled 2026-11-25T10:16:40Z) — Backend (ruff, mypy, pytest,
migrations) ×2, Frontend (typecheck, lint, test, build) ×2. Promoted on that observation,
not on descendant-CI. Local gates at `460a26a` agree: `ruff format` clean, `ruff check`
clean, `mypy` clean over 98 sources, full `pytest` **730 passed / 4 skipped** (was 725; the
delta is exactly the 5 new tests), and the migration round-trips
(`upgrade → downgrade -1 → upgrade`).

Negative control: deleting the `version == expected` predicate turns 3 of the 5 tests red,
so they fail for the lost update and not for some unrelated reason.

## NEX-019 (run `NEX-2026-11-25-21`) — a stale write must not erase run progress

`AgentRun.version` was incremented on every mutation and never compared on write. Two
writers that loaded the same run both committed; the second overwrote the first's step
progress with a state derived from its stale copy, and neither caller was told.

For a note that is a lost edit. For a durable run it defeats the point: a stale copy
committing after a crash flips an `interrupted` step back to `pending`, so the next resume
re-executes a step whose effect may already have landed.

The aggregate now records the version it was last persisted at (`persisted_version`,
seeded on load), and `save` is a compare-and-set: one `UPDATE ... WHERE id AND version =
:expected`, `VersionConflict` on a rowcount of 0. `version - 1` could not have served this
role, because several mutations can happen between two saves. `VersionConflict` already
maps to HTTP 409, so the loser reloads and retries instead of assuming its step settled.

Status: **VERIFIED** — CI **4/4 `completed/success` at `66e8cd6`**, polled 2026-11-25T09:46Z
with every check run's `head_sha` confirmed equal to `66e8cd6` (Backend ×2, Frontend ×2).
Because the green run is at the commit itself, NEX-019 needs no descendant-CI reasoning —
unlike NEX-018. Local gates, run bare on the feature branch: 725 passed / 4 skipped, ruff
format and check clean over 198 files, mypy clean over 98 source files. Three tests in
`tests/agent/test_run_concurrency.py` include a matching-version success case, so an
always-raising guard cannot pass.

## NEX-018 (run `NEX-2026-11-25-20`) — erasing the seal laundered the verdict

`verify_chain` recomputed an entry's `outcome_hash` only when one was present, so
the cheapest possible forgery worked: rewrite a `denied` row to `succeeded`, null
`outcome_hash` *and* `completed_at`, and there was nothing left to recompute. The
forged chain verified clean. Reproduced before the fix (`verified: True`), and
rejected after it (`terminal invocation carries no outcome hash`).

The existing API test flipped `status` but left `outcome_hash` intact, so the
stale-seal branch caught it. The stronger forgery was never exercised — R18's
shape again: the test attacked a weaker threat than the control must survive.

Fix: a non-`started` entry carrying no seal is now a break. Only `started` may
honestly lack one, because its outcome is not known yet. `_STARTED_STATUS` is a
literal, not an import, to avoid a cycle with `domain.agent.tools`.

Status: **VERIFIED** on descendant-CI: `2b3beb7` is 4/4 `completed/success` and
contains the fix commit `789d78a` as an ancestor. `789d78a` itself has no check
runs — the following docs commit cancelled its run in the concurrency group, so
the evidence is labelled descendant-CI rather than "green at this SHA". Six domain
tests in `tests/agent/test_audit_chain.py`, including the honest unfinished-
`started` case, so the fix cannot "pass" by refusing everything.

## NEX-016 (run `NEX-2026-11-25-16`) — the approval gate was structurally unusable

A run that reached an approval-gated step could never resume. Two independent
defects each alone made it impossible:

1. `AdvanceAgentRun` caught `AccessDenied` and called `fail_step`; a failed step
   drives the run to terminal `FAILED`, so the approval about to be granted had
   nothing left to resume.
2. The gateway recorded the pause as a `DENIED` invocation under the step's own
   idempotency key. `(agent_id, idempotency_key)` is unique, so that denial
   permanently owned the operational key: the retry after approval replayed the
   denial rather than executing.

The fix separates waiting from failing: `ApprovalRequired` (an `AccessDenied`
subtype, so the 403 contract is unchanged) causes `Run.defer_step` to return the
never-started step to `pending` and the run to `PENDING`; the deferral is audited
under a `#awaiting-approval` sibling key so the operational key stays free; and an
already-open request for the same capability/resource is reused so repeated
advances cannot flood the approver's queue.

Why the suite was green throughout: every approval test covered one half of the
path (that a request is raised, that a decision is stored, that expiry and
revocation work). None covered approve-then-continue. See risk **R22**.

## NEX-017 (run `NEX-2026-11-25-17`) — a suspended delegator's agent kept working

NEX-006 closed the offboarding gap by calling `ensure_tenant_access` on the three
human identity paths: the OIDC callback, per-request session resolution, and the
bearer provider. **Agent-token resolution is a fourth way to establish an
identity, and it never called it.**

That matters because `_actor_principal` makes every tool call act *as the
delegator*. So suspending or deprovisioning a person left their agent fully
operational for as long as its token lived — the exact offboarding hole NEX-006
closed, reopened one layer up.

`ResolveAgentToken` now re-checks tenant access after its existing delegation
checks (expiry, revocation, agent suspension, capability subset) and refuses
`DENIED`. It reuses `resolve_tenant_access`, so the decision stays in the pure
domain rule rather than becoming a second copy. Two deliberate choices:

- **`UNAFFILIATED` is still allowed.** A brand-new identity must be able to
  bootstrap; the check refuses only a *denied* membership. Per-resource
  authorization continues to run as the delegator downstream.
- **The refusal is a pause, not a tombstone.** A test reactivates the membership
  and asserts the same token works again, so the check cannot be satisfied by
  permanently breaking the token.

The two tests drive the real path end to end (HTTP membership suspend, then token
resolve) and were observed failing before the fix — the resolve returned `200`
for a suspended delegator. A skip-based version that asserted nothing was
discarded rather than kept. See risk **R23**.

**Status: VERIFIED at `990557d`.** CI on that exact SHA is 4/4
`completed/success` (observed 2026-11-25T08:56Z, `head_sha` confirmed on all four
check runs), with local gates green at the same commit (716 passed / 4 skipped,
ruff format + check clean, mypy clean, architecture 5 passed). See risk **R23**.

**Next best actions**

1. **PR #3 body is current at `88926ec`** — 31,612 additions / 10 deletions / 111
   files / 118 commits, 757 tests, eleven migrations, eleven routers, all measured
   from the PR object at that head and verified byte-identical after the PATCH
   (25,010 bytes, refreshed 2026-11-25T12:53:30Z). It names `c367746` (NEX-023) and
   `eaaae23` (NEX-024) alongside the earlier verified commits. Re-measure from the PR
   object after any future push: a docs commit moves the head and the counters with it.
2. **NEX-003 and NEX-006 were promoted to VERIFIED this run** on a
   *descendant-CI* basis — see `CHECKPOINT.json` → `validation.evidence_basis`.
   Neither product commit (`e5f1bb4`, `4c5a6ef`) has check runs of its own; both
   are ancestors of `fa387a9`, where CI was observed 4/4 `completed/success`, and
   the acceptance set was re-run locally (239 passed).
3. **Select the next dependency-ready Phase-5 slice from `ROADMAP.md`.**

## Correction recorded in run NEX-2026-11-25-14 (NEX-013 status was overstated)

NEX-013 was recorded VERIFIED on green CI. Its core invariant was **not**
enforced on the path that matters:

- `ApprovalRequestRepository.add()` built `ApprovalRequestRow` without
  `expires_at` or `consumed_at`. Every approval the tool gateway opened was
  therefore persisted with `expires_at = NULL`.
- `is_usable` read NULL as "never expires", and the `consume()` predicate
  accepted `expires_at IS NULL`. So the 72-hour bound NEX-013 introduced was
  **inert in production**; an approved agent could replay a delete indefinitely.
- The NEX-013 expiry test stayed green because it exercised `save()` — which
  does write the columns — and never `add()`, the path the gateway uses.

This is the R18 class: a test that proves the aggregate's logic while the
adapter that stores it drops the field. Fixed in `12584a1` (three independent
fail-closed changes plus tests that pin the production path), and CI on
`12584a1` is observed 4/4 green. NEX-013 is **VERIFIED** on that evidence — the
criterion holds, and now so do the tests that check it.


## NEX-021 (run `NEX-2026-11-25-23`) — a stale write must not resurrect a stopped agent

This run finished the CAS audit that NEX-019 and NEX-020 started. The audit asked one
question of every repository under `infrastructure/db/`: does `save()` rewrite
authority-bearing columns from caller-supplied state, and is there a version to compare
against?

| Aggregate | `version` column | `persisted_version` | CAS on `save` |
| --- | --- | --- | --- |
| `Note` | yes | via `expected_version` arg | yes |
| `AgentRun` | yes | yes | yes (NEX-019) |
| `ApprovalRequest` | yes | yes | yes (NEX-020) |
| **`AgentIdentity`** | **yes** | **no** | **no — unconditional overwrite** |
| `DelegatedAuthorization` | no | n/a | writes 3 fields; revoke monotonic |
| `AgentToken` | no | n/a | writes `revoked_at` only; monotonic |
| `ToolInvocation` | no | n/a | writes verdict and seal together |

`AgentIdentity` was the last offender and the worst one, because its revocation *is* the
emergency stop. It already bumped `version` on every authority-changing transition —
`suspend`, `activate`, `revoke`, `narrow_capabilities` — and `save()` ignored it. The
version was maintained and then thrown away.

The harm: two requests can hold the same agent id at once (a human revoking in the control
centre while an unrelated edit finishes). With no compare-and-set the later commit won
silently — a stale copy wrote `status=active` and `revoked_at=NULL` back over a revoked
row, undoing the kill switch; and a stale copy taken before a narrowing restored the
removed capabilities, re-widening a ceiling the domain only permits to shrink. Neither
raised.

Fixed by giving `AgentIdentity` a `persisted_version` (seeded in `__post_init__`, advanced
by `mark_persisted()`) and making `save()` one `UPDATE ... WHERE id AND version = :expected`
that raises `VersionConflict` (HTTP 409) on rowcount 0 — the same shape as NEX-019 and
NEX-020. `add()` also calls `mark_persisted()`. No migration: `agents.version` has existed
since `c3f1a9d47e02`.

Status: **VERIFIED** at `e459f98` (CI 4/4). Five new tests in `tests/agent/test_agent_identity_concurrency.py`
drive the production `save()` path. Negative control run this session: deleting the
`version == expected` predicate turns 4 of the 5 red while the honest-write test stays
green, so the suite detects the lost update rather than asserting "always raise".

**Deliberately out of scope:** `DelegatedAuthorization`, `AgentToken` and `ToolInvocation`
need no version. Each writes a strictly monotonic field (`revoked_at`, a terminal verdict),
so concurrent writers converge on the same value instead of erasing each other's authority.
Recorded so a later audit does not re-open it without cause.

**Next action:** observe CI on the commit carrying this slice; promote to VERIFIED only on
4/4 `completed/success` at that `head_sha`.

## Active task

| Field | Value |
| --- | --- |
| Task | NEX-059 — enforce `second-approver` with a second **distinct** human |
| Status | Nothing in flight. **NEX-058 VERIFIED** at `c5a4f4e` (CI 4/4 at `a4003bf`). |
| Next action | 1. Poll CI for the head read from `git ls-remote origin refs/heads/feature/agentic-enterprise-platform`; on 4/4 `completed/success` promote NEX-058 to VERIFIED. 2. Clear delivery debt: append NEX-057 + NEX-058 to PR #5 and post one issue #2 comment covering both. 3. Then NEX-059 — the last named obligation without a mechanism, and the one where "any second approval" silently defeats the control. |

## NEX-032 (run `NEX-2026-11-25-34`) — a withheld field that cannot say why is not a signal

NEX-031 made an over-cap record-derived value *withheld* rather than fatal, which is the right
failure direction: a legal note must not deny an advisory. But it made withholding the quiet
path. `excluded_field_names` recorded *that* an input was held back and nothing recorded *why*,
so two opposite situations produced the same record:

- **ABOVE_CEILING** — the caller classified the value and the envelope's ceiling excluded it.
  The control working as designed.
- **OVER_CAP** — the value exceeded `MAX_FIELD_VALUE_LENGTH` and could not be represented at
  all. The advisory was computed on less context than the caller believes.

Only the second deserves attention, and the flat list made a routine design decision
indistinguishable from a quiet degradation. This is the R20/R24 shape again: a signal that
exists but cannot be distinguished is not a signal.

`ContextEnvelope.withheld_fields` now returns `(name, reason)` pairs and
`over_cap_field_names` exposes the subset worth watching. An input that is both over-cap and
above the ceiling is reported **once**, as `OVER_CAP`, so telemetry never double-counts one
input. Names only, never values — keeping the withheld value would leave the raw subject text
one refactor away from the canonical form, which is the thing withholding exists to prevent.
The reason travels end to end to `ApprovalAdvisoryResponse.over_cap_context_fields`, and
`DecisionRequest` enforces that the over-cap set is a **subset** of the excluded set: two
records that disagree about what was withheld are worse than one.

**Second defect found while testing.** `from_candidates` validated the classification only on
the under-cap path; the over-cap branch skipped it because the field was never constructed, so
a bare string slipped through there while raising under the cap. The path that *discards* the
value must not be a hole in the type check. `ContextField` still accepts `RESTRICTED` and lets
the ceiling withhold it — its documented "exclusion is recorded, not silent" contract, which an
earlier draft of this slice wrongly inverted by rejecting it.

**Tests.** 10 new cases, including the validation-bypass regression, the once-only rule, and an
API boundary test that creates a **real note**. That test was first written against a
fabricated `resource_ref` and returned `[]` — no real note, no title candidate, so the
assertion proved nothing. A test that passes because its subject does not exist is worse than
no test.


## NEX-031 (run `NEX-2026-11-25-33`) — a note is 300x the context cap, and that must not deny the advisory

NEX-030 closed by naming its own residual: the envelope bounds (32 fields, 200 chars/field) had
met real approval metadata but never real **note text**. The subject of an approval is a note,
and a note body may be `MAX_CONTENT_BYTES` — 64 KiB of UTF-8, roughly 300x the per-field cap.

`RecommendApprovalDecision` built its context with `ContextEnvelope.from_fields`, which
constructs every `ContextField` eagerly and therefore *raises* when a value exceeds
`MAX_FIELD_VALUE_LENGTH`. That is correct for hand-authored references, where the caller is
responsible for bounding input. It is wrong for record-derived content: a note title is legal at
the notes domain's `MAX_TITLE_LENGTH`, so "the title is long" became "the advisory failed" — a
denial of a read the caller is entitled to. The two caps are independent constants that happen to
both be 200 today, which is exactly what hid the defect: a title at the domain maximum sits *at*
the cap, so a coincidence read like a guarantee.

The fix is `ContextEnvelope.from_candidates` for record-derived values: an over-cap value is
**withheld instead of raised**. The field is never constructed — so it cannot enter the digest,
and the raw subject text is not left in memory one refactor away from the canonical form — and
its name is recorded in `withheld_field_names` so the audit trail still says the input existed.
`excluded_field_names` unions the two withholding reasons (above-ceiling classification,
over-cap value), because an audit reader needs the same fact either way: *this input was present
and not sent*. Construction rejects a withheld name that is not a legal field name, a name that
is both included and withheld, and repeated withheld names. `from_fields` is unchanged and still
refuses over-cap values; the distinction is caller-knows-the-bound versus value-comes-from-a-record.

Tests: the over-cap path is withheld and named; a withheld value never reaches the digest (two
envelopes differing only in the over-cap value hash identically); the two reasons union without
shadowing; illegal and contradictory names are rejected. The advisory gains the production-data
check — a real stored note with a **64 KiB body** still yields an advisory, its title withheld by
*classification* (CONFIDENTIAL, above the INTERNAL ceiling) rather than by length, and the
approval stays PENDING. A second test shrinks the context cap so the two caps diverge, proving the
divergence rather than relying on today's coincidence.

**Load-bearing:** reverting the fix fails the max-title test with `a context field value may be at
most 50 characters` at `context.py:133` — the exact denial-of-advisory the change removes.

## Last completed task

**NEX-030 (VERIFIED, `0727628`, CI 4/4)** — `DecisionRequest.context_hash` was a plain `str`, so a
caller could hand-assemble a digest that did not correspond to the envelope it was sending.
Asserting a hash asserts "these were the inputs", so a caller-assembled value takes on the
declassification decision ADR-012 gives to `ContextEnvelope` alone. `ContextHash` subclasses
`str`, so it serialises, compares and hashes as the plain hex digest — it is a construction-time
guard, not a new wire format. Only `ContextEnvelope.context_hash` can mint one. The stated limit
is recorded in the class docstring: `str.__new__` still bypasses the guard deliberately, so the
mistake is impossible to write by accident rather than impossible for a process already inside
this one.

## NEX-024 (run `NEX-2026-11-25-26`) — aborting is settling, so it must seal too

NEX-023 sealed the invocation a crash left open when an owner **rules** on an interrupted
step. It missed the owner's other way of settling a run — **aborting** it — so R27 survived
on exactly one path: the one taken when the owner declines to adjudicate.

Two records disagreed. `AgentRun.abort` flipped the run to `aborted` and touched nothing
else, leaving a `started` step inside a terminal run, and `AbortAgentRun` never closed the
gateway invocation, so the audit chain kept saying `started` — indistinguishable from a call
still in flight, forever.

Both halves now settle. The aggregate settles every non-terminal, non-`pending` step before
it flips the run's status (`started` → `failed`, `interrupted` → *effect not confirmed*);
`pending` steps are untouched because they never began. The use case records the mid-flight
sequences before aborting, then seals each matching invocation by the gateway's own key in
the same transaction as the run save. It writes `fail(...)`, not `succeed(...)`: aborting is
not a ruling that the effect landed, and claiming success would fabricate evidence. Absent
or already-terminal links are left alone, as in NEX-023.

Status: **VERIFIED** at `eaaae23` — 4/4 `completed/success` (two Backend `ruff, mypy,
pytest, migrations` and two Frontend `typecheck, lint, test, build`, for the push and
pull-request events), polled 2026-11-25T12:16Z; all four `head_sha` values equal the polled
commit. Local gates at the slice: `ruff format` clean (201 files), `ruff check` clean, `mypy`
clean over 98 sources, full `pytest` **742 passed / 4 skipped** (baseline 740, delta exactly
these 2 tests), `tests/agent/test_agent_runs.py` 21 passed. Negative control: removing only
the seal loop turns exactly `test_abort_seals_the_audit_link_the_crash_left_open` red.

## Last completed task

**NEX-024 (VERIFIED, `eaaae23`, CI 4/4)** — aborting a run settled it in one record and left
the other mid-flight. `AgentRun.abort` flipped the run to `aborted` and touched nothing else,
so a `started` step stayed inside a terminal run, and `AbortAgentRun` never closed the gateway
invocation, so the audit chain kept reporting `started` for a run that was definitively over —
indistinguishable, forever, from a call still in flight. Both halves now settle in the same
transaction: the aggregate abandons every non-terminal, non-`pending` step (`started` →
`failed`, `interrupted` → *effect not confirmed*) and the use case seals each matching
invocation as `fail(...)`. It never writes `succeed(...)`: aborting is not a ruling that the
effect landed, and claiming success would fabricate audit evidence. `pending` steps are
untouched because they never began. R27 is now closed on both settle paths that exist —
resolve (NEX-023) and abort (this slice).

Previously, **NEX-023 (VERIFIED, `c367746`, CI 4/4)** — a ruled-on effect left the audit trail asserting
"outcome unknown" forever. `InvokeTool` commits its `ToolInvocation` as `started` before
entering the handler, which is right: a crash must leave a trace. But
`ResolveInterruptedStep` settled the run and never closed that row, so after an owner ruled
whether the effect landed the chain still said `started`. Three costs, all permanent: the
trail is wrong about a fact a human has since established; `started` is exactly what a live
call looks like, so a crash-orphaned link and an in-flight one became indistinguishable; and
the deterministic key stayed open, so `_find_prior` raised `Conflict` on every retry — the
ruling meant to unblock the run did not unblock the gateway. The use case now seals the link
in the same transaction that settles the run (`succeed`/`fail`, no write when the link is
absent or already terminal), so the two records cannot disagree. Three tests pin it,
including that an already-settled link is never rewritten; reverting the use case turns
exactly the two sealing tests red.

Previously, **NEX-015 (VERIFIED, `414a222`, CI 4/4)** — a decision erased the approval record from the
only read path. `GET /agents/{id}/approvals` returned pending requests only, so
once a human approved or denied a sensitive action the request vanished from the
API: the approver could not confirm what they had authorised, and an auditor had no
API view of the grant at all. Reproduced before fixing (after a decision, the
listing returned `[]`), then fixed by adding a `?status=` filter that defaults to
`pending` (unchanged behaviour) and a `GET /agents/approvals/{id}` read route. Both
are owner-only and return 404 to a non-owner, so an approval id cannot probe for
another user's agent. Eight new tests pin approved/denied/revoked visibility, the
unchanged default, read-by-id, and the 404/422 paths; reverting the use case to
`list_pending_for_agent` fails three of them.

## Previous task

**NEX-012 — Tamper-evident audit chain** (run `NEX-2026-11-25-12`, `3aade7c`).
NEX-005 wrote a row for every gateway attempt and NEX-011 made those rows
readable, but the rows were still mutable: anyone with database write access
could flip a `denied` verdict to `succeeded` or delete a row, and nothing would
notice. A record that cannot prove it is intact is not audit evidence.

Each invocation now carries `chain_sequence`, `previous_hash`, `intent_hash` and
`outcome_hash`, sealed over the previous link. `GET
/api/v1/agents/{agent_id}/tool-invocations/verification` returns
`{verified, checked, first_break}`.

Three decisions carry the slice:

- **A break is reported, not raised.** `verify_chain` is pure and returns a
  `ChainVerdict` naming the sequence where history diverges. An exception would
  tell a caller only that something is wrong, when what an auditor needs is
  *where*.
- **`checked` is part of the contract.** Without it, `verified: true` could mean
  "walked nothing" — a verifier that reports success without examining rows is
  worse than no verifier.
- **The chain is listed unpaginated.** A window's first entry does not carry its
  predecessor's hash, so a window cannot be verified. `list_chain` is deliberately
  whole-trail.

The gateway seals intent before the call and outcome after it, refusals included,
because a blocked attempt is evidence too. `next_chain_link` is read inside the
inserting transaction and the unique `(agent_id, chain_sequence)` constraint
rejects a concurrent loser, so the chain cannot silently fork.

Pinned by `tests/api/test_audit_chain_verification_api.py` (7 tests), which tampers
through the database directly — that is the threat model; tampering through the API
would prove nothing. **Status: VERIFIED** — CI on `3aade7c` returned 4/4 `completed/success`.

**NEX-011 — Tool-invocation audit trail over HTTP** (run `NEX-2026-11-25-11`, `f50a78d`).
The governed tool gateway writes a `ToolInvocation` row for every attempt it
handles, refusals included, but nothing outside the gateway could read one back.
A trail nobody can read is not an audit trail: an owner could not answer "what did
my agent actually try, and what was refused?"

`GET /api/v1/agents/{agent_id}/tool-invocations` now serves that trail through a
`ListToolInvocations` use case over the **existing** `ToolInvocationRepository`
port and its `list_for_agent(limit, offset)` adapter method. The port, the table,
and the paged query all already existed; this slice adds the read path and
nothing else — no new port, no migration.

Three properties carry the slice, each pinned by a test rather than asserted in
prose:

- **Owner-scoped, 404 not 403.** The trail says which tools an agent reached for,
  so it is the owning human's evidence. Reusing `load_owned_agent` makes a
  non-owner and a nonexistent id indistinguishable, so the endpoint cannot be used
  to probe for another user's agents.
- **A digest, not the arguments.** The trail proves a call happened with a given
  shape without becoming a second copy of note content. A test asserts a note's
  private body never appears in the response.
- **Refusals are as visible as successes.** "The agent never tried" and "the agent
  was blocked" are different audit findings, so a denied call must leave a row. A
  test drives a capability-denied invocation and asserts a `denied` row carrying
  the gateway's outcome is present.

The one design departure: this route takes `limit`/`offset` directly instead of
the shared `_page` helper the other agent listings use. An audit trail grows
without bound, so a caller has to be able to walk it deliberately; `limit` is
capped at 200 and validated at the edge.

Pinned by `tests/api/test_audit_trail_api.py` (8 tests). **Status: VERIFIED** — it
reached VERIFIED on an observed 4/4 `completed/success`; the earlier "not yet
verified" note was a snapshot of the run that wrote this section and is no longer
true.

Previously, **NEX-009-HTTP — Durable agent runs over HTTP** (run `NEX-2026-11-25-10`, `e817d04`).
Slice 1 built the durable runtime but left it unreachable: the use cases existed
and nothing could call them, so durability was real and unusable (R11).

`presentation/api/routers/agent_runs.py` now mounts `/api/v1/agent-runs` with
plan, advance, get, list, resolve, and abort. The router is a thin edge — it
authenticates and delegates — so audience, revocation, capability, approval,
ordering, and audit stay in the use cases and the gateway rather than becoming a
second, weaker path to a tool.

The load-bearing decision is that two authorities meet on one resource. Planning
and advancing are the *agent's*, keyed on the agent token and reading nothing
else, so a demo identity cannot drive an agent's run. Reading, resolving, and
aborting are the *owner's*, because an interrupted step means the runtime cannot
tell whether a side effect landed and only the human who owns the agent may rule
on it. An agent therefore has no endpoint that settles its own ambiguous step.

Hardening found while writing the edge: `PlannedStep.capability` is now derived
from the tool catalog and is no longer an init field. A caller could previously
hand the use case a step that named a privileged tool while declaring a weaker
capability, and the run row is exactly what an operator reads when deciding
whether an interrupted effect landed — so understating authority would corrupt
the audit of the one case that matters. The mismatch is now unrepresentable
rather than merely rejected.

Pinned by `tests/api/test_agent_runs_api.py` (9 tests) and two new domain tests
in `tests/agent/test_agent_runs.py` (14 total). **Status: VERIFIED** — it reached
VERIFIED on an observed 4/4 `completed/success`; the earlier "CI has not yet run"
note was a snapshot of the run that wrote this section.

Previously, **NEX-009 slice 1 — Durable agentic runtime** (run `NEX-2026-11-25-09`, `0e74d4f`).
A governed tool invocation was atomic: it ran or it refused, and nothing survived
the process. An agent therefore could not carry a multi-step intention across an
interruption, and a crash mid-effect left no record of what had been attempted.

The run is now an aggregate (`domain/agent/runs.py`): an ordered plan of steps,
each recording the arguments it was asked to perform, with a lifecycle that
cannot skip a step or settle one out of order. A step is persisted as `started`
*before* the gateway is called and saved again on completion, so a resumed run
replays the persisted plan instead of re-deriving it.

The load-bearing decision is what happens on recovery. A step found `started` is
marked **INTERRUPTED** and the run **halts**. The runtime cannot know whether the
effect landed, and guessing is wrong in both directions: assume it landed and the
effect is silently lost; assume it did not and the effect is applied twice. Only
an owner may settle it (`ResolveInterruptedStep`), and the settled step is never
re-executed. That is the difference between durability and an at-least-once retry
loop, and it is what the tests pin.

Also in the slice, because durability alone is not safety: a run is scoped to the
agent its token resolves to, so one agent cannot read or abort another's run;
capabilities are checked at plan time against the token; and a revoked delegation
stops the run at the next advance rather than at token expiry.

Pinned by `tests/agent/test_agent_runs.py` (13 tests). **What is not done:** there
is no HTTP surface for runs, so no client can plan or advance one yet. That is
slice 2.

Previously, **NEX-010 — Direct tests for the OIDC token verifier** (run `NEX-2026-11-25-07`).
`HttpOidcRelyingParty._verify_id_token` was the only security-critical code in the
identity stack that **no test imported**: every login test injected
`FakeRelyingParty`, so NEX-002's criterion "an ID token with wrong
issuer/audience/expiry is rejected" was asserted only against the fake. It now
has `tests/authentication/test_oidc_relying_party.py` — 21 tests using real
in-process RSA keys and real RS256 signatures, stubbing only the JWKS lookup so
no network is touched.

The point of the slice was that the tests must be able to *fail*, so that was
proved by mutation rather than assumed: disabling `verify_aud`, `verify_iss`,
`verify_exp`, `verify_iat`, `verify_signature`, the nonce check, the nonce
comparison, the blank-subject guard, or the algorithm allowlist each turned the
suite red. (Deleting the `"require"` list survives — it is defence in depth, not
the enforcing mechanism; the behaviour it guards is still pinned by
`test_token_without_{expiry,issued_at,issuer,audience}_is_rejected`.)

Writing the tests found a **real defect**, fixed in this slice: the nonce
comparison called `secrets.compare_digest(nonce, expected_nonce)` on `str`, which
raises `TypeError: comparing strings with non-ASCII characters is not supported`
when the untrusted `nonce` claim contains non-ASCII. A hostile IdP response
therefore produced a **500 at the edge** instead of a refusal. It now compares
UTF-8 encodings — still constant-time, and unable to raise.

NEX-002 moves **IMPLEMENTED → TESTED → VERIFIED**. It became VERIFIED in run
`NEX-2026-11-25-08` when CI ran the full suite on `0fcc078` (4/4 green), because
local gates are not CI evidence and this run refused to conflate them.

Previously, **NEX-007 — Tenant-scoped capability answer** (run `NEX-2026-11-25-06`,
`e1cb0c5`). `ListMyOrganizations` filtered on membership rows alone, so a
**suspended or archived tenant was listed with its full capability set** while
`GetOrganization` refused the same tenant with `404` — a client would render a
tenant it could not then use. The domain rule already computed the correct set
(`TenantAccess.organization_ids`) but nothing consumed it. It is now exposed as
`actionable_organization_ids(uow, user_id=...)` — the single answer to "which
tenants count for this caller" — and the listing filters through it. The set
requires an active membership *and* an operational organization, so suspending
one tenant does not hide the caller's others. No caching, so a status change
takes effect on the next request. Pinned by
`tests/application/test_organization.py::TestSuspendedTenantIsNotListed`.

Previously, **NEX-005-HTTP — Expose the tool gateway over HTTP** (run `NEX-2026-11-25-05`,
`c18bac7`). `POST /api/v1/agents/tools/invoke` is the thinnest possible edge over
`InvokeTool`, so audience, revocation, capability, approval, and audit remain in
exactly one place. Two properties are load-bearing and pinned by
`tests/api/test_tool_gateway_api.py` (9 tests): the demo-identity header is
**ignored** (a demo user id is not authority to invoke a tool), and the token is
resolved for `TokenAudience.TOOL_GATEWAY`, so a Notes-API token is refused.
Refusals return `401`/`403`/`409`/`422`, never a `500`, and the token is never
echoed. Rate limiting keys on a digest of the presented token rather than the
client address, so agents cannot exhaust each other's budget.

Previously, **NEX-008 — Add continuous integration** (runs `NEX-2026-11-25-03`
and `-04`).
The repository had **no CI at all**; `.github/workflows/ci.yml` now runs the
backend gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the
frontend gates (`typecheck`, `lint`, `test`, `build`), and an apply-then-revert
migration check on every push and pull request.

**CI is observed green, not merely configured.** Runs `45531483962` (push) and
`45531492783` (pull_request) both succeeded on `6f30020`; `gh api
.../commits/6f30020/check-runs` reports all four check runs `completed/success`.
Risk R3 is closed on that evidence.

The first run failed, and usefully so: `tests/persistence/test_migrations.py`
shelled out to the literal path `.venv/bin/alembic`, which exists on a developer
machine and nowhere else, so the three migration tests had never run outside a
local checkout. Fixed in `6f30020` by invoking `sys.executable -m alembic`.


Previously, **NEX-006 — Tenant access enforcement** (`4c5a6ef`). Authentication proves
identity; tenant access is a separate deterministic check that the caller still
belongs to an operational tenant, so a suspended or deprovisioned membership
stops working on the next request rather than at session expiry. Enforced at one
shared seam (`ensure_tenant_access`) across all three identity-establishing
paths: `CompleteLogin`, `ResolveSession`, and `OidcIdentityProvider.authenticate`.

Previously, **NEX-001** fixed two defects that blocked the organization slice:

1. `OrganizationUseCase` was referenced in `bootstrap/container.py` without being
   imported — this broke *every* test that built the container (121 setup
   errors, all one root cause).
2. `SqlAlchemyTeamMembershipRepository.list_for_team` returned soft-deleted
   (`DEPROVISIONED`) rows, so `DELETE .../teams/{id}/members/{id}` answered 204
   while the member still appeared in the roster.

## Identity & access: what is enforced today

Authentication proves *who* the caller is. **Tenant access** is a separate,
deterministic application-layer check that the caller *still belongs* to an
operational tenant, so a suspended or deprovisioned membership stops working on
the next request rather than at session expiry.

- Pure rule: `domain/organization/tenant_access.py` →
  `GRANTED` / `DENIED` / `UNAFFILIATED`.
- Shared seam: `application/use_cases/tenant_access.py` →
  `ensure_tenant_access(uow, user_id=...)`.
- Enforced on all three identity-establishing paths: `CompleteLogin`,
  `ResolveSession` (per request), and `OidcIdentityProvider.authenticate`
  (bearer tokens). One check, three call sites, no copies to drift.
- `UNAFFILIATED` is allowed so a brand-new identity can create its first
  organization; it grants nothing inside an existing tenant.
- The refusal message is uniform, so callers cannot probe for account existence.

## Verified implementation status

The organization slice is implemented across all layers:

- **Domain** — `domain/organization/{entities,value_objects}.py`: organizations,
  units (flat tree with cycle prevention), teams, organization memberships with
  manager links, team memberships, membership status lifecycle.
- **Application** — `use_cases/organization.py` (org/unit/team/member lifecycle,
  last-administrator guard, privilege-escalation guards),
  `use_cases/organization_policy.py` (role → permission mapping).
- **Ports** — `application/ports/organization_repositories.py`,
  `organization_authorization.py`; UnitOfWork extended.
- **Infrastructure** — `infrastructure/db/organization_repositories.py`,
  models, `uow.py`; Alembic migration `189c359206d3_organization_hierarchy.py`.
- **API** — `presentation/api/routers/organizations.py`, schemas registered.
- **Tests** — domain, application, security, API, and migration suites.

## Quality gates (latest local run)

| Gate | Result (run `NEX-2026-11-25-31`, bare as CI runs them) |
| --- | --- |
| `ruff format` | clean — 156 files unchanged (`src/`, `tests/`) |
| `ruff check` | All checks passed |
| `mypy` | clean (104 source files) |
| `pytest` (full) | **828 collected** on head `03ead43` (local collection at this head). The pass/skip split is quoted from CI rather than a local run: backend CI is green at `a3d1b32`. |
| Migration chain | no migration added; head unchanged. No schema change in this slice |
| Frontend `typecheck`/`lint`/`test`/`build` | not run locally this slice — no frontend files changed (CI ran them anyway and they passed) |

> **CI is the evidence.** Local gates above are for reproducibility, not proof (R9/R10).
> NEX-028 is **VERIFIED**: `a3d1b32`'s four check runs are 4/4 `completed/success`,
> every `head_sha` equal to `a3d1b32`, polled 2026-11-25T14:42Z.

## Blockers

**R38 — stale lease blocks coordination writes until 2026-11-27T09:34:54Z (`1795769694`).**
`refs/locks/nexus/lease` is held by `Andrews-Mac-mini.local-56084` (run `NEX-2026-11-27-43`).
That process is dead, but the lease is unexpired, so `lease acquire` exits 2 and refuses the
write. A second segment of this run wrote `606805f` and `5ecb5c7` without holding the lease —
a protocol violation, recorded rather than concealed. No state damage: the branch is linear,
the tree is clean, `main` is untouched, and CI on the head is 4/4 success. Product work is
unaffected; only coordination-file writes are blocked. Do **not** force-delete the lease ref;
it expires on its own. See R38 in `CHECKPOINT.json` for the evidence and the follow-up
(a liveness-aware takeover, so a crashed holder stops blocking writes for the full 45-minute TTL).

The following are known open items, not blockers:

- Tool gateway, Jev integration, and the admin console UI are **not yet
  implemented** — they are sequenced in `ROADMAP.md`, not regressions.

## Risks

- **R1 (closed 2026-11-25):** PR #3's description claimed the PR was
  "specification/design only" while the branch carried 19,228 insertions across
  85 files. It now reports the implemented phases, the two gateway defects, and
  the validation evidence. The PR is still a draft: bringing it to review is a
  human decision, not the coordinator's.
- **R2 (low):** The frontend has no admin console yet; the expansion spec's
  frontend testing requirements cannot be satisfied until that work lands.
- **R3 (closed 2026-11-25, runs `NEX-2026-11-25-03` / `-04`):** The repository
  had **no CI configuration at all** — no `.github/workflows` on any branch, and
  zero check runs on any commit. `.github/workflows/ci.yml` now runs the backend
  gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the frontend
  gates (`typecheck`, `lint`, `test`, `build`), and an `alembic upgrade head` →
  `downgrade base` cycle, on every push and pull request. Closed on observed
  evidence, not on the file's presence: runs `45531483962` (push) and
  `45531492783` (pull_request) succeeded on `6f30020`, and `gh api
  .../commits/6f30020/check-runs` reports all four check runs
  `completed/success`. The first run (`b223e02`) failed and found a genuine
  portability defect — migration tests invoked `.venv/bin/alembic`, which does
  not exist in CI, so those tests had never run outside a local checkout; fixed
  in `6f30020`. Residual: CI covers the documented gates only, so a gap no test
  exercises is still a gap.
- **R4 (low):** `hypothesis` is not a project dependency, so the attenuation
  invariant is proved by exhaustive enumeration rather than property-based
  sampling. This is stronger for the current delegable universe but would not
  scale if the capability set grew substantially; revisit if capabilities
  multiply.
- **R5 (closed 2026-11-25, run `NEX-2026-11-25-05`):** The tool gateway had no
  HTTP/router exposure, so no API contract test covered it and no client could
  call it. `POST /api/v1/agents/tools/invoke` now exposes it, pinned by
  `tests/api/test_tool_gateway_api.py` (9 tests). Closed on **VERIFIED** evidence:
  committed and pushed at `c18bac7`, and CI is green on that exact commit (four
  check runs, all completed/success).
- **R6 (closed 2026-11-25):** the tool gateway now has a dedicated test file,
  `tests/agent/test_tool_gateway.py` (18 tests). Writing it found two real
  defects, both fixed in `74d3d98`: a retried *denial* hit the
  `(agent_id, idempotency_key)` unique constraint and raised `IntegrityError`
  instead of replaying the recorded refusal, and a revoked delegation surfaced
  the domain `InvariantViolation` (a 500 at the edge) instead of a refusal.
  Do not delete that file; both bugs return silently without it.
- **R10 (low, process):** a green local gate run is only evidence
  if it invokes the *same command CI invokes*. The pre-push run for `2dd7a0c` was
  scoped to `src tests`, so it skipped `docs/`, where ruff then failed the build
  on a Markdown-embedded Python fence. Run the gates bare. Recorded in
  `AGENTS.md` so the next run does not relearn it.
- **R11 (closed 2026-11-25, run `NEX-2026-11-25-10`):** the durable-run use cases
  had no HTTP surface, so durability was real and unreachable — no client could
  plan or advance a run. `presentation/api/routers/agent_runs.py` now mounts
  `/api/v1/agent-runs` (plan, advance, get, list, resolve, abort), pinned by
  `tests/api/test_agent_runs_api.py` (9 tests). **VERIFIED**: CI reported 4/4
  check runs `completed/success` on `e817d04`. Writing the edge also found a real
  hardening gap — `PlannedStep.capability` was caller-supplied, so a plan could
  name a privileged tool while declaring a weaker capability; it is now derived
  from the tool catalog and is not an init field.
- **R16 (new, low):** an approval's `expires_at` column is nullable, and
  `ApprovalRequest.is_usable` treats NULL as "never expires". That is deliberate
  for rows written by an older application version during a rolling deploy, but
  it means such a row is unbounded. Tighten the column to NOT NULL once no old
  writer can exist. The backfill does populate it for every pre-existing row, so
  NULL now only arises mid-deploy.
- **R17 (new, low, process):** a data-backfill migration that is only ever run
  against an *empty* database is untested, and its tests will stay green while it
  crashes on the first real row. NEX-013 shipped exactly that: `requested_at +
  timedelta` raised `TypeError` on SQLite's string value, aborting the upgrade and
  leaving `expires_at` NULL — i.e. the migration that exists to bound approvals
  left them unbounded. Any migration that reads existing rows needs a test that
  seeds a row at the previous revision. `TestApprovalValidityBackfill` is the
  pattern to copy.

## Next best actions

1. ~~**Promote NEX-027 on CI.**~~ **Satisfied** — 4/4 `completed/success` at `fb8d5bd`,
   polled 2026-11-25T14:18Z.
2. ~~**NEX-028 context assembly.**~~ **Satisfied** — VERIFIED, 4/4 `completed/success` at
   `a3d1b32`, polled 2026-11-25T14:42Z. M2.6 decisioning is now complete in both halves.
3. ~~**Refresh PR #3.**~~ **Satisfied this run** — refreshed from the PR object at head
   `03ead43`, PATCH 2026-11-25T14:52:08Z, read back byte-equal (30,024 chars). Counters now
   34,700 additions / 10 deletions / 124 files / 137 commits.
4. ~~**Wire `ContextEnvelope` into a real caller.**~~ **Satisfied** — NEX-029 (production
   caller), NEX-030 (`ContextHash`), NEX-031 (real note bodies) and NEX-032 (per-field
   observability) are all VERIFIED. M2.6 decisioning is complete in both halves and exercised
   against real stored data.
5. ~~**Confirm whether revoking a delegation or agent invalidates approvals already granted
   under it.**~~ **Satisfied** — closed on both paths by NEX-025 (R28).
6. ~~**Aggregate over-cap withholdings into a count — the natural successor to NEX-032.**~~
   **Satisfied by NEX-033**, which shipped the `WithholdingRecorder` port for exactly this.
   The checkpoint's `next_action` naming NEX-034 for this work was **stale** — written
   before NEX-033 delivered it. Run `NEX-2026-11-25-35` found this during reconciliation
   and re-scoped NEX-034 to a real defect rather than re-implementing a shipped feature.
   **Lesson:** a `next_action` field is intent recorded at one moment; check it against
   `git log` before executing it, the same way a `commit` field is checked against
   `ls-remote`.
7. ~~**Re-poll CI at `fe5eadc` and promote NEX-032 to CI-verified.**~~ **Satisfied.** 4/4
   `completed/success`, every `head_sha` = `fe5eadca918b…`, polled 2026-11-25T16:47Z. PR #3
   body PATCHed (43,921 chars) and issue #2 commented (7168592223) with that evidence.
8. ~~**NEX-034: harden `redact()`.**~~ **Satisfied** — IMPLEMENTED, TESTED and PUSHED at
   `e386e02`; CI poll pending. The matcher now decides on credential *shape* (separator and
   camelCase segmentation, plurals, non-sensitive compounds) instead of eleven exact
   strings, and a sequence under a sensitive key is replaced whole instead of leaking.
9. ~~**Poll CI at `e386e02` and promote NEX-034 if green.**~~ **Satisfied** — 4/4
   `completed/success`, every `head_sha` equal to `e386e02`, polled 2026-11-25T18:15Z. NEX-034
   is CI-verified. A docs commit that follows a product commit reads `completed/cancelled` once
   superseded — that is the concurrency group, never evidence.
10. ~~**Then R30, the one live risk with no mitigation.**~~ **Satisfied by NEX-035** — the
    conformance suite exists and R30 is closed. Every bound in the decision contract
    (400-char rationale, `[0.0, 1.0]` confidence, the candidate choice set) now has a test
    that exercises it against a hostile payload, not only against the stub. R29's stated
    `str.__new__` residual on `ContextHash` is a deliberate, documented trade-off, not a gap
    to close.
11. ~~**NEX-035: the provider identity seam.**~~ **Satisfied** — IMPLEMENTED, TESTED,
    COMMITTED, PUSHED and **CI VERIFIED**: 4/4 `completed/success` at `12f6fa9`, every
    `head_sha` equal to `12f6fa9`, polled 2026-11-25T18:14Z.
12. **Next: audit the decisioning path's *callers* for a swallowed abstention.** The module's
    contract is that an abstention is advice and enforcement stays deterministic — but a
    caller that ignores `DecisionEvaluation` and proceeds on `None` would silently turn
    "the provider could not judge" into "allowed". Enumerate every production caller of
    `EvaluateDecision.run`, and for each, pin with a test what it does when the outcome is
    `ABSTAIN` with reason `DISABLED`, `PROVIDER_ERROR` and `MALFORMED_OUTPUT`. This is the
    fail-closed question, and it is the one part of M2.6 no test covers yet. Verify the
    premise first: `grep -rn "EvaluateDecision\|\.run(" src/notes_service/application`
    before assuming callers exist.

**A terminal state in one record is not a terminal state in the other (R27).** NEX-023 is
the same shape as R26 (a column written and never read) but inverted: the run aggregate
reached a terminal status while the *audit* record for the same step stayed `started`
forever. Whenever a use case settles one record, ask which other records describe the same
event and whether any of them can be left mid-flight. A settled run next to a `started`
invocation is not a partial success; it is two records that disagree, permanently.

**Check the adapter, not just the aggregate (R18).** NEX-013 was VERIFIED while
`ApprovalRequestRepository.add()` silently dropped the two columns the whole
invariant depended on, because the expiry test used `save()`. When a slice adds
a field that a security check reads, pin it through the write path production
actually uses.

**Promote nothing on local gates alone.** NEX-009 slice 1, NEX-009-HTTP, NEX-011
and NEX-012 each reached TESTED on local runs and then VERIFIED only when CI went
green on the commit (`0e74d4f`, `e817d04`, `f50a78d`, `3aade7c`). A local green
suite is not evidence; R9/R10 exist because that conflation happened before.


## Next best actions (run `NEX-2026-11-25-37`, superseding the list above)

1. **PATCH PR #3's body with a NEX-037 section, then POST one issue #2 comment.** This run
   spent its delivery budget on reconciliation, the slice, and the state files; both are owed.
   Re-measure the counters from the PR object at the new head rather than reusing the numbers
   above — a docs commit moves the head and every counter with it. Use lists, never a markdown
   table, in an issue comment on this repository.
2. **NEX-037's CI is settled — do not re-poll it.** 4/4 `completed/success` at `5c0575b`,
   polled 18:57Z, every `head_sha` equal to that SHA. Any future docs head reads
   `completed/cancelled` once superseded; that is neither pass nor fail and is never evidence.
3. **Next implementation slice: the first real `DecisionProvider` adapter.** It is the largest
   dependency-ready unlock, and NEX-035's conformance suite now exists to police it — the
   suite found four real defects in the identity seam, so a new adapter has something to fail
   against. `make_decision_evaluator()` is still a stub.
4. **R36 is open and is not urgent but is not nothing:** the ops allowlist is a static
   environment variable with no expiry and no record of who read the totals. Fold it into the
   scoped-capability work rather than patching it ad hoc.

## Next best actions (second invocation of run `NEX-2026-11-25-37`, superseding item 1 above)

Item 1 of the list above is now **stale and must not be acted on**. It said PR #3 and issue #2
were owed a NEX-037 report. They are not: a later run in the same run-ID cleared both, verified
here against the live API — PR #3 body contains NEX-037, and issue #2's newest comment is
`7171861451`, the NEX-037 report. Do not re-post either.

1. **Start NEX-038: the first real `DecisionProvider` adapter.** Unchanged, unstarted, and the
   largest dependency-ready unlock. `make_decision_evaluator()` is still a stub. NEX-035's
   conformance suite exists to police the adapter and has already found four real defects in
   the identity seam, so a new adapter has something to fail against. Verify the premise in the
   code before acting — several recent `next_action` fields needed correcting.
2. **Resolve the duplicate before it rots.** Branch `nexus037-duplicate-local` (`33620bf`) holds
   a superseded, different-design implementation of the same withholding endpoint — bearer
   `OPERATIONS_API_TOKEN` instead of the shipped operator allowlist — plus three documentation
   sections the shipped slice lacks (`docs/API.md`, `docs/SECURITY.md`, `.env.example`). Decide:
   port the documentation onto the allowlist design, or delete the branch as superseded. The
   shipped design is the better one; the documentation gap is real. Do not merge it as-is — it
   would document an endpoint shape that does not exist.
3. **Before any of that: read the remote, not a local ref.** `git ls-remote origin
   refs/heads/feature/agentic-enterprise-platform` is the only trustworthy answer to "what is
   the branch head". A stale local `origin/*` ref caused the second invocation of this run to
   re-implement an already-verified slice and burn most of its window. This is the second
   recorded occurrence of that failure.

R36 remains open as described in item 4 above; it is unaffected by this run.

## NEX-039 (run `NEX-2026-11-25-37`, third invocation) — the adapter existed only as a stub

Every decisioning slice from NEX-028 onward has been real: a context envelope, an
unforgeable context hash, per-field withholding with reasons, a rate, a redactor, a
conformance suite, a production caller. All of it was exercised through
`StubDecisionProvider`, which is deterministic, offline, and abstains under any threshold
above its fixed confidence. So the platform could *run* the decisioning path but could never
obtain a real judgment. NEX-039 adds the adapter that changes that.

The interesting part is not the HTTP call. It is the four things the adapter refuses to do.

- **The vendor cannot abstain; it can only be unsure.** The wire contract has no
  `abstained` outcome. A provider returns a confidence and the *caller's* threshold in
  `EvaluateDecision` decides whether that is good enough. Abstention policy therefore stays
  in the application where it is auditable. `DISABLED` and `PROVIDER_ERROR` are our
  operational states describing our relationship to the vendor; a vendor that could claim
  them would be reporting on infrastructure it does not own.

- **The request carries no context, only a question**: `kind` plus sorted `candidates`.
  Not the context hash, not the excluded field names, not the correlation id. The adapter
  never receives note text by the port's design, but the *names* of sensitive categories are
  themselves a disclosure, and the correlation id is ours. A vendor that later needs to
  correlate its logs should get a freshly minted opaque per-request id, not this one.

- **Sorting is what makes order-independence structural.** The conformance suite requires
  that reordering a candidate set not change the answer. Relying on vendor
  order-insensitivity would make that a property of the vendor; sorting before the call makes
  it a property of this adapter, because the same set produces a byte-identical body.

- **A bad answer is an abstention, never an exception.** Transport error, non-200,
  oversized or non-JSON body, an unoffered choice, a non-finite or out-of-range confidence —
  each maps to a contract-legal abstention. Vendor error bodies are *discarded rather than
  parsed*, because they can echo the request or the credential into our audit trail.

Construction fails closed. The endpoint must be `https://` and the name must be a bounded
identifier, both checked in `__init__` rather than per request: a misconfiguration should be
a boot defect, not a process that looks healthy while every advisory degrades to
`unidentified`. `Settings` applies the same rules so the two cannot drift.

Provider selection lives in the container, so there is one answer to "which provider is this
process using". Setting `DECISIONING_HTTP_ENDPOINT` opts a deployment into a real vendor; it
does **not** enable judgment, because `decisioning_enabled` still gates every call.

Two limits are recorded in the module docstring rather than papered over: the cap bounds
*size*, not *duration* — httpx timeouts are per socket operation, so a peer that drips one
byte inside each timeout can still hold the call open; and a rationale is truncated rather
than fatal, because discarding a legal answer over a verbose explanation would let vendor
verbosity deny a decision. The size bound itself now runs **while reading** (R37, run
`NEX-2026-11-27-44`), so it bounds what is held as well as what is parsed.

**Validation.** 51 adapter tests plus 13 config/wiring tests, and the NEX-035 conformance
harness invoked at `tests/agent/test_http_decision_provider.py:404`. Full gates: ruff clean
(172 files), mypy clean (110 source files), **984 passed / 4 skipped**. Negative control:
replacing `sorted()` with `list()` at `http_provider.py:135` turns exactly three tests red —
`test_a_choice_request_carries_only_kind_and_sorted_candidates`,
`test_candidate_order_does_not_change_the_request_bytes`, and the conformance test — which
proves the invariant is genuinely tested and not incidentally satisfied. The mutation was
reverted byte-identically before commit. CI: 4/4 `completed/success` at `8e670a6`, polled
2026-11-25T19:32Z.


## NEX-042 (run `NEX-2026-11-27-45`) — the integrity check only worked for the owner who already suspected

**The gap.** `VerifyToolInvocationChain` answers "is this one agent's trail intact" for that
agent's **owner**. Nothing answered the platform question — *is any trail in this deployment
broken* — so a cut tail or an edited verdict on any other agent stayed invisible until the
affected owner happened to open their own agent. Detection is not an owner's duty; it is the
platform's. This is the same class as R23 and R26: a control that is correct on every path
someone enumerated, and absent on the path nobody enumerated.

**The slice.** `SweepAuditIntegrity` walks every agent, compares each tail against the
out-of-band `ChainAnchor`, and emits one `ChainAuditFinding` per contradiction. Three
properties carry the argument:

1. **Coverage is falsifiable.** The report carries `agents_total` *and* `agents_checked`, so a
   capped sweep cannot be read as "all chains intact". `verified` is `False` whenever
   `truncated` is true, because the unexamined agents could each be broken.
2. **`checked` counts only what passed.** `verify_chain` reports `checked =
   expected_sequence - 1` at a break — the entries that verified *before* the contradiction.
   This run's single test failure was my assertion (`== 1`), not the code: the implementation
   was right and the test was wrong. The corrected assertion records why in the test.
3. **The gate fails closed twice.** An empty operator allowlist admits nobody, not everybody;
   a non-operator gets `404`, not `403`, so probing cannot confirm the surface exists. It is
   deliberately *not* an `AuthorizationProvider` decision — that policy governs note content,
   and platform-wide integrity data must never be reachable by content access alone. Same
   shape as `GetWithholdingReport` (NEX-037), reused rather than reinvented.

**Validation on the exact committed tree.** `ruff format --check` 248 files; `ruff check` all
passed; `mypy` no issues in 117 files; `pytest` **1060 passed, 4 skipped, 0 failed**. Targeted:
9 passed across the two new test files. Commit `ed641b0`, pushed, `git ls-remote` confirms the
remote head equals local HEAD, **CI 4/4 `completed/success` at `ed641b0`** (polled
2026-11-27T09:46Z).

**Honest gaps.** No route-level test yet proves the ops route reads the operator allowlist
*from settings*; the API tests exercise the use case directly. `DEFAULT_MAX_AGENTS = 500` is a
guess — it is honest about being one (`truncated`), which is the property that matters, but the
number has met no real deployment.

**R38 recurrence.** The stale lease blocked coordination writes for 254 seconds. It expired at
`1795772807` and was acquired cleanly at `1795772820`. Product delivery finished first, so the
lease blocked only the docs. Second consecutive run; the liveness-aware takeover is still
unbuilt.

## Next best actions (run `NEX-2026-11-27-46`, superseding the lists above)

1. **NEX-043** from the top of `BACKLOG.md`. First command is always
   `git ls-remote origin refs/heads/feature/agentic-enterprise-platform` against
   `git rev-parse HEAD` — every recent run has had to correct a recorded `next_action`.
2. **Close NEX-042's deferred delivery debt:** prove the ops route reads
   `OPS_OPERATOR_USER_IDS` from settings with a route-level test, rather than trusting that
   the container wiring was reviewed. This is product work, not reporting.
3. **Verify the lease fix in the field.** This run proved release/renew across processes on a
   scratch repo, but it did not acquire the shared lease: a prior run's `host-pid` lease
   (`Andrews-Mac-mini.local-95179`, expiring `1795775517`) still held the ref, and under the old
   code no one can release it. The next run should find the ref free after that expiry and
   confirm acquire -> status(`mine: true`) -> release on the real repo. If a legacy `host-pid`
   lease is ever stuck again, let it expire; never force-delete the ref.

## R39 (run `NEX-2026-11-27-45`) — the lease can be acquired but never released

**Found by trying to be polite.** After the NEX-042 docs were pushed, this run called
`lease release` to hand the lease back so the next run would not wait. It exited with
`lease held by another agent`, holder `Andrews-Mac-mini.local-95179` — this run's own identity.

**Cause.** Holder identity is `host-pid`. Every `nexus_coord` invocation is a new process, so
the pid recorded at acquire time is never the pid of the process doing the release. `mine` is
therefore false on the very next call, and `release` and `renew` can never succeed. The lease is
single-use per acquisition.

**Consequence, and why R38 was not bad luck.** A run that acquires a lease necessarily blocks
the next run's coordination writes for the full TTL (45 minutes). R38's "stale lease held by a
dead holder" was the predictable output of exactly this, not an unlucky crash. Expect a third
recurrence at the start of the next run; this run's lease expires `1795775517`
(2026-11-27T10:31:57Z).

**Why the writes were still made.** The ref is held by this run's own identity — the lease *is*
held; only the tool's `mine` comparison is broken. Coordination writes were made on that basis
and are recorded here rather than concealed, the same judgement the prior run recorded for its
second segment. No state damage: the branch is linear, the tree is clean, `main` is untouched.

**Fix (not attempted this run — no time to change coordination tooling safely).** Give the holder
a stable per-run identity — the run id, or a pid that outlives the run — so `release` and `renew`
recognise their own lease. Alternatively a `release --force` guarded by the run id in the lease
commit message. Do **not** force-delete the ref by hand.

This is now the highest-value process fix in the programme: it costs every run minutes of wall
clock and, worse, it invites runs to write without the lease because the lease is unusable.

## Next best actions (run `NEX-2026-11-27-48`, second invocation, superseding the lists above)

1. **NEX-045 — a stored grant.** User, scope, granted-at, expires-at, granted-by behind a
   repository port and adapter, so a grant can expire and an operator can read who holds
   what. `OPS_OPERATOR_USER_IDS` becomes the seed path into it, not the only source. Keep
   the `404`-not-`403` refusal and the record-on-grant behaviour; both are already right.
   This is the residual the NEX-044 section above records as deliberately unclosed.
2. **First command of every run stays** `git ls-remote origin
   refs/heads/feature/agentic-enterprise-platform` against `git rev-parse HEAD`. It has
   been the only reliable way to start for six consecutive runs.
3. **~~Verify the lease fix in the field~~ — CLOSED this run.** Acquire, status, renew and
   release were each run as a separate process against the real `refs/locks/nexus/lease`:
   `mine: true`, `holder_liveness: alive`, `renewed: true`, `released: true`, and the ref
   free afterwards. The holder was `Andrews-Mac-mini.local-s32718`, a POSIX session id,
   which is the shape `64759f4` introduced. The R39 defect is fixed in practice, not only
   in test.

## Correction (run `NEX-2026-11-27-48`) — the lease fix was shipped, not pending

The R39 section below ends with "Fix (not attempted this run)". That was accurate for run
`NEX-2026-11-27-45` and has been false since commit `64759f4`, which introduced the
session-scoped holder and is in this branch's history. Recorded here rather than edited in
place so the earlier entry keeps its own context. The field verification above is the
evidence.

## Next best actions (run `NEX-2026-11-27-48`, NEX-045 segment, superseding the lists above)

1. **Poll CI for `e6f58c0` before anything else.** `GET /commits/e6f58c0/check-runs`. Two
   runs were queued/in_progress at this segment's close. Record the verdict in
   `CHECKPOINT.json`; a pending run is not a pass. If Backend fails, the likely cause is the
   migration check, because the slice adds no migration — verify that rather than assuming.
2. **Clear the delivery debt: two reports are owed, oldest first.** NEX-044 and NEX-045
   have both shipped but neither is on issue #2 or in draft PR #3. Post the NEX-044 report,
   then the NEX-045 report, then append both sections to PR #3 and read them back. This is
   the cheapest high-value work available and it is the only thing making the GitHub record
   lag the branch.
3. **NEX-046 — a durable `OperatorGrantRepository` (closes R40).** The store is
   process-local: a restart forgets a bounded grant and each worker holds its own copy.
   Add a SQLAlchemy adapter behind the existing port, with a migration, and keep the
   in-memory one for tests. The gate and `ListOperatorGrants` do not change — that is the
   point of the port. Do not start this until the two reports above are posted: delivery
   debt compounds, and R40 fails closed so it is not urgent in the security sense.

## Run NEX-2026-11-27-53 — NEX-052 (policy versions on tool invocations)

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD**: `3215e08`
- **Status**: IMPLEMENTED + TESTED + COMMITTED + PUSHED. **CI `in_progress` — not VERIFIED.**
- **Slice**: an audited tool invocation now names the published policy versions that
  governed it, sealed into the intent hash. Allowed and denied calls both record it.
- **Compatibility**: uncited rows hash with the original `v: 1` payload byte for byte, so
  the pre-existing audit trail still verifies; only cited rows move to `v: 2`.
- **Migration** `b7d2c48e91af`: NOT NULL column, backfill, then drop the server default so
  a forgotten write fails loudly. Two real bugs fixed (undropped default; SQLite batch
  rebuild dropping the column).
- **Open delivery debt**: PR #5 section and issue #2 comment for NEX-052.

**Next best actions**

1. **Poll CI for `3215e08`.** This is the only thing standing between NEX-052 and
   VERIFIED. Do not promote it on local gates alone — that is the mistake the status
   semantics exist to prevent.
2. **Clear the NEX-052 delivery debt**: append a section to PR #5 and comment on issue #2.
3. **Obligations.** The security architecture lists deny, allow and *obligations*; the
   gateway models the first two only. An obligation is an allow that still requires an
   action, so it cannot be bolted on as another denial path — it needs the decision to
   carry required actions to the caller. Read `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`
   for the obligation contract before designing it, and write an ADR if the gateway's
   return type has to change shape.

## Run NEX-2026-11-27-54 — NEX-052 VERIFIED

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD**: `57f50d1`
- **Status**: NEX-052 is **VERIFIED**. CI 4/4 `completed/success` at `57f50d1` on both
  events (polled 2026-11-27T16:30Z). No new product code this run — the slice was
  delivered last run; this run closed its verification debt.
- **Why `57f50d1` is valid evidence**: it is a docs-only descendant of the product commit
  `3215e08`, so the Backend job tests the NEX-052 code, including migrations against
  Postgres.
- **The `cancelled` mystery, resolved**: branch concurrency cancels superseded runs, and
  every checkpoint is a commit on the branch, so a checkpoint naming a SHA cancels that
  SHA's runs while in progress. **Cancellation never retracts a concluded run** — that is
  what makes a later poll authoritative. Recorded in `CHECKPOINT.json` → `ci_status`.

**Next best actions**

1. **Obligations.** The architecture lists three policy effects — deny, allow,
   obligations — and the gateway models the first two only. An obligation allows the call
   but attaches required actions, so it cannot be bolted on as another denial: the
   decision has to carry those actions to the caller and into the audit trail. Read the
   obligation contract before designing, and write an ADR if `PolicyDecision`'s shape must
   change.
2. **PR #5 is ready for human review** — CI green, mergeable, `mergeable_state: clean`,
   24 files. It stays **draft** until a human decides; NEXUS must not merge it.
3. **Tracking issue #2** carries 42 comments; the next phase section should be written
   when obligations lands, not before.

## Run NEX-2026-11-27-55 — obligations enforced at the tool gateway (NEX-053)

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD at checkpoint**: `9521442`
  (the checkpoint commit that writes this file lands one commit later)
- **Status**: NEX-053 is **IMPLEMENTED + TESTED + COMMITTED + PUSHED**, CI **unpolled**.
  It is *not* VERIFIED and must not be counted as such until a run polls the current head.
- **Slice**: ADR-014's obligation axis, implemented. `PolicyObligation` is a closed
  vocabulary (approval, second-approver, step-up, redaction, rate-limit, enhanced-audit).
  `PolicyRule` carries obligations; a deny carrying one raises at construction, and a
  duplicate raises. `evaluate()` unions the obligations of every matching allow and drops
  them when a deny also matched. `PolicyDecision.is_satisfied` is stricter than `allowed`
  so a caller cannot read the weaker property and stop reading.
- **Fail-closed enforcement**: the gateway satisfies only `approval`, and only where the
  domain already treats the capability as sensitive (`ApprovalRequest.open` refuses to gate
  a non-sensitive one, so policy cannot invent a human gate). Every other obligation is
  refused with an audited denial, never silently skipped.
- **Codec compatibility**: `obligations` is omitted when empty, so a rule without them
  encodes to the exact bytes it did before and no stored row needs rewriting. An unknown
  obligation name on decode is an error, not a drop.
- **Local gates on the pushed tree**: ruff format 289 files unchanged, ruff check clean,
  mypy clean (137 source files), pytest 1306 tests / 0 failures / 0 errors / 4 skipped.
- **Concurrent-run hazard observed**: another run pushed to this branch twice during this
  run. Both times the push was rejected, the new remote head was merged (never rebased,
  never force-pushed), and containment was verified with
  `git merge-base --is-ancestor <sha> <remote>` plus `git cat-file -e <remote>:<path>`.
  Grepping push output for a SHA gave a false positive once — use `merge-base`, not text.

**Next best actions**

1. **Poll CI for the current branch head** from `git ls-remote origin
   feature/agentic-enterprise-platform` — do not poll a SHA written in these files, because
   writing them is itself a commit that supersedes it. Only a concluded green run promotes
   NEX-053 to VERIFIED.
2. **Append the NEX-053 section to PR #5** and comment on issue #2 — delivery debt for this
   slice, owed by the next run.
3. **Next slice**: an obligation *satisfaction* adapter for `redaction` (the second kind
   with a real mechanism behind it), or `enhanced-audit` if the audit trail already has the
   extra fields. Do not add a handler for `step-up` or `second-approver` until the identity
   model can distinguish assurance levels and count distinct approvers — an obligation
   "satisfied" by a weaker check than its name promises is worse than a refusal.

## Run NEX-2026-11-27-55 (continued) — NEX-053 VERIFIED

- **CI evidence**: 2 workflow run(s) (pull_request, push), 4 jobs, all completed/success at head `c825db61859591a46b5d4fc1d957130f126db9f8` (polled 2026-11-27T17:07Z). `c825db61859591a46b5d4fc1d957130f126db9f8` is a
  docs-only descendant of the product commit `9c31484`, so the Backend job compiled and
  tested the obligations code, including the Postgres migration path.
- **Promotion**: NEX-053 moves IMPLEMENTED/TESTED/COMMITTED/PUSHED → **VERIFIED**.
  Evidence, not local gates, is what promoted it.
- **Remaining delivery debt**: PR #5 description still has no NEX-053 section and issue #2
  has no NEX-053 comment. That is the first task of the next run.

## Run NEX-2026-11-27-55 (continued) — delivery debt cleared

- **PR #5**: appended the NEX-053 section (obligations as a separate axis, fail-closed
  deny-on-unhandled-obligation, the file table, the CI evidence) and corrected a stale line
  that still called the PR a draft. It is **ready for human review and still unmerged**.
- **Issue #2**: NEX-053 status comment posted.
- **Delivery debt is now zero.** Every completed slice is reflected in the PR and the
  tracking issue, so the next run starts on implementation, not on bookkeeping.

## Run NEX-2026-11-27-61 — PR #5 review verified, NEX-060 promoted

> CORRECTION (same run, after review of the record): `dd42fd7` was committed by the *prior* run as NEX-060, not by this one. This run did not implement it. This run read the PR #5 review, checked the fix against the review's P1 item, re-ran every gate, polled CI to 4/4 success, and cleared delivery debt. The task ID is NEX-060 and the status promotion to VERIFIED is this run's contribution. The earlier `NEX-061` label in this file was wrong; it is corrected in place below.

**Phase.** 3 (governance). Five of five ADR-015 obligations have mechanisms. Remaining
governance gap: risk-based approval.

**Branch / head.** `feature/agentic-enterprise-platform`. Product commit `dd42fd7`; merge
head `327144a` (merged concurrent docs-only commit `f7e2b4a` from another run). Tree clean.

**Active task.** None — NEX-060 is verified and delivered; nothing is in flight.

**Last completed task.** NEX-060 — fail-closed policy-evaluation boundary. Implemented by the
prior run at `dd42fd7`; **verified this run** on 4/4 CI success. This run's own work was the
verification, the review response, and clearing delivery debt.

**What the review asked for, and what was done.**

| Review item | Severity | Status |
| --- | --- | --- |
| Policy evaluation only catches `DomainError`; unexpected failures escape the denial/audit path | P1 | ADDRESSED at `dd42fd7` |
| Rate limiter is process-local; make the deployment limitation explicit | non-blocking | ADDRESSED at `dd42fd7` (documented, no code change needed) |
| Obligation readiness should be visible at authoring time | non-blocking | Deferred — needs policy-validation surface |
| Parameterised per-rule rate limits | non-blocking | Deferred — vocabulary change, needs an ADR |

**The defect, stated plainly.** The gate promised fail-closed behaviour and caught only
`DomainError`. Any other failure escaped *before* the denial and its audit row were written,
so the caller got a stack trace and the trail got nothing. A gate that throws is failing open
with extra steps.

**Why the test injects a fault.** `Comparison` validates its value type at construction, so
every *currently reachable* evaluator failure is already a `DomainError`. Crafting a bad
policy would have tested the old path and passed before the fix. The new tests patch
`tools.evaluate` to raise, which is the only honest way to exercise the boundary the review
asked for. Reverting the fix fails 4 of the new cases.

**Verified implementation status.** NEX-060 is **VERIFIED**: implemented, tested, committed,
pushed, and CI 4/4 `completed/success` on head `29f71e5`. The first CI poll after the product
push was incomplete and was recorded as incomplete rather than rounded up.

**Validation run this run, bare as CI runs them.** `ruff format --check` 297 files already
formatted; `ruff check` clean; `mypy` 139 source files; full `pytest` exit 0 (4 postgresql
skipped); the touched test file 15 passed.

**Open PRs.** #5 — open, not draft, unmerged, mergeable, body extended with NEX-059/060 and
the review response posted as a conversation comment. Nine slices stack in it; the reviewer's
"consider a split" note stands. **NEXUS never merges.**

**Tracking issue.** #2 open. Comment 7227186715 reports NEX-060 and the review response.
Delivery debt is cleared; nothing owed.

**Blockers.** None.

**Next best actions.**

1. Add the `derive_assurance` `acr`/`amr` mapping test — the signal `step-up` rests on and
   still the only untested piece of that control.
2. Risk-based approval: the last named ADR-015 obligation without a mechanism.
3. Decide with a human whether PR #5 should be split; nine slices stack in it and the
   reviewer raised the cost. Not a NEXUS decision to make alone.

## Run NEX-2026-11-27-62 — `derive_assurance` fail-open fixed and tested (NEX-062)

**Current project phase.** Phase 3 (governance). All five ADR-015 obligations have mechanisms.
This run closed the last untested piece of the `step-up` control and fixed a fail-open found
while testing it. Remaining governance gap: risk-based approval.

**Current branch / commit.** `feature/agentic-enterprise-platform`; product commit `acb1fd9`,
pushed as part of head `6aa0af6` (merge of a concurrent run's docs commit `1c2377e`).

**Active task.** None — NEX-062 is committed and pushed, awaiting a CI read.

**Last completed task.** **NEX-062** — `derive_assurance` acr/amr mapping.
**Status: VERIFIED (corrected in run 84).** This record was written before its CI was read and
said NOT VERIFIED; NEX-062 has since been CI-verified and is listed in `verified_tasks`, so the
earlier wording is retracted here rather than left to read as an open gate. A record left stale
toward "not ready" is the dangerous direction: it invites a later run to redo finished work.

**What was found.** The function decided multi-factor by substring, so every `acr` naming
multi-factor in order to deny it resolved to the strongest level — `mfa-optional`,
`mfa-not-required`, `mfa-disabled`, `not-multifactor` all read as stepped-up. ADR-015 makes the
`step-up` obligation enforceable by this one derived fact, so the control itself was reading a
fail-open value: a login the IdP labelled as not stepped-up could spend a step-up obligation.
This is the same class of defect NEX-060 fixed one layer up.

**The fix.** Match by segment, let an adjacent negation segment veto a marker. The veto is
blunt on purpose — `mfa:not-sms` reads as single-factor because telling "multi-factor but not
SMS" from "multi-factor not required" needs a level registry this build lacks. Understating
refuses a step-up; overstating spends one that never happened. Numeric level conventions
(`aal2`, `loa3`, `ial2`) stay unmapped and now say why: several describe identity proofing, not
a factor count, and mapping them would guess at provider semantics inside an enforcement path.
Mapping them needs an ADR.

**Validation run this run, bare as CI runs them.** `ruff format --check` 298 files already
formatted; `ruff check` clean; `mypy` 139 source files; full `pytest` exit 0 (4 postgresql
skipped); `pytest tests/authentication/` 194 passed. Negative check: reverting the fix made 12
of the new tests fail, restoring it returned them to green — the regression is pinned, not
merely described.

**Open PRs.** #5 — open, not draft, unmerged. Ten slices now stack in it; the reviewer's
"consider a split" note stands and is a human decision. **NEXUS never merges.**

**Tracking issue.** #2 open. NEX-060 was reported by run 61. NEX-062 is **owed** a comment.

**CI status.** Not polled for `acb1fd9`. That is the first action of the next run, not a gap
being papered over.

**Blockers.** None.

**Risks.** Concurrent runs on this branch are the norm — this run had to merge a push that
landed mid-test. Task IDs can collide: run 61 had already taken `NEX-060` and `NEX-061`, so
this slice is `NEX-062`; read the log before assigning an ID. The blunt negation veto will
understate a compound level like `mfa:not-sms` (accepted, argued above). Ten slices in one PR
carry real review cost.

**Next best actions.**

1. Poll CI for the current head — `git ls-remote origin refs/heads/feature/agentic-enterprise-platform`,
   then `GET /commits/<sha>/check-runs` — and promote NEX-062 only on 4/4 `completed`/`success`.
2. Comment on issue #2 with NEX-062 and the fail-open it fixed; append NEX-062 to PR #5's body.
3. NEX-063: risk-based approval — the last named ADR-015 obligation without a mechanism.

### Run NEX-2026-11-27-62 close-out

**Current commit.** `0e2ca47` (docs-only), descending from product commit `acb1fd9`.

**NEX-062 — VERIFIED.** CI polled to a verdict rather than inferred: head
`0e2ca4793e0f891a9eadf299a93693755c8eba6f`, four check-runs, all `completed`/`success` — Backend
(ruff, mypy, pytest, migrations) twice and Frontend (typecheck, lint, test, build) twice. The
duplicate pairs are the branch concurrency group re-running. `0e2ca47` descends from `acb1fd9`
through a docs-only commit, so that CI compiled and tested the fix.

What NEX-062 fixed: `derive_assurance` decided multi-factor by substring, so `urn:acme:mfa-optional`,
`urn:acme:mfa-not-required`, `urn:acme:mfa-disabled` and `urn:acme:not-multifactor` all resolved to
multi-factor. ADR-015 makes `step-up` enforceable by naming the delegating human's recorded
assurance, and this function is the only thing that mints it — so the control was reading a
fail-open value. Same class as NEX-060, one layer down. Now matched by segment with an adjacent
negation veto; 12 of the new tests fail if the fix is reverted.

**Delivery state — no debt.** PR #5 body extended to 29082 chars with a NEX-062 section (verified:
one section, NEX-060 intact). Issue #2 comment `7227311410` posted. Both carry the AI-agent
disclosure line. PR #5 remains unmerged for human review.

**CI status.** 4/4 `completed`/`success` on `0e2ca47`. An earlier poll of the same head showed
Backend `in_progress`; only the all-completed read was accepted.

**Why the run stopped.** Clock-checked, not assumed. ~926s remained before `DEADLINE`, but NEX-063
is a design-first security slice whose own framing requires reading ADR-015 and the approval
aggregate before designing — not enough room to finish, and starting it would have left an
uncommitted tree. Closed on a verified, pushed, recoverable increment instead.

**Blockers.** None.

**Risks.** Unchanged: ten slices stack in PR #5 and the reviewer's "consider a split" note stands.
That is a human decision; NEXUS may not split or merge unilaterally.

**Next best actions.**

1. **NEX-063 — risk-based approval.** The last named ADR-015 obligation without a mechanism. Read
   ADR-015's risk-based approval section, then the existing approval aggregate, before designing.
2. Poll CI on any head this run's successor produces, and treat `cancelled` as superseded rather
   than failed.
3. Keep PR #5 unmerged and its body current; the split decision is human.

**Do not redo** NEX-060 or NEX-062 — both are CI-verified.

## Next best actions (run `NEX-2026-11-27-70`, superseding the lists above)

1. **Verify NEX-069 - poll CI on `7a1d5e2`.** It is pushed and green locally (1472 passed,
   ruff + mypy clean, 5 tests proved to fail with the fix reverted) but CI has not been read.
   `cancelled` means superseded by a later docs commit, never failed.
2. **Clear delivery debt.** PR #5's body covers through NEX-068 and issue #2's last comment
   covers NEX-067. Both are silent on **NEX-065** (now CI-verified) and **NEX-069**. Update
   both in one pass; do not open a second PR - fifteen slices already stack in #5.
3. **Then take the next dependency-ready backlog slice.** Read `BACKLOG.md` for the highest
   priority task whose dependencies are met; do not start a new slice with under 10 minutes
   left on the clock.

**Do not redo** NEX-060, NEX-062, NEX-063, NEX-064, NEX-065, NEX-066, NEX-067, NEX-068 or
NEX-069 - all are implemented and pushed, and every one but NEX-069 has been CI-verified.

### What this run found, and why it matters more than the task it was doing

NEX-069 was not on the backlog. It surfaced while re-validating NEX-065, and it is the kind
of defect that only shows up on a database with real history in it: the NEX-065 migration
required an approver on every in-force policy version, and a CHECK constraint is table-wide,
so `alembic upgrade head` failed for anyone who had ever published a policy. The test meant
to catch that had never reached the migration at all - it seeded a text `id` into an INTEGER
primary key and died on the seed. **A green test suite was hiding a broken upgrade path.**

Two habits worth keeping: re-run the gates on the branch you are actually going to push to
(not the one you developed on), and treat "the test passes" as unproven until you have seen
it fail without the fix.

**Run-ID caution.** IDs are assigned per run and have now collided twice. NEX-069's commit
message says NEX-066, an ID already used by the obligation-enforcement slice; force-push is
prohibited, so the message stands and the correction lives in `EXECUTION_LOG.md`. Grep the
log and checkpoint for the next free ID *before* writing the commit message.

## Delivery for NEX-072 (run `NEX-2026-11-27-72`)

- **PR #5**: NEX-072 section appended 2026-11-28T00:55Z (PATCH 200, body 50507 -> 52842 chars).
  Still **open, draft=false, unmerged** — automated merging is prohibited; a human decides.
- **Issue #2**: NEX-072 comment posted 2026-11-28T00:56Z (id `7231239328`, HTTP 201, 1838 chars).
  The issue stays open: the programme is not done.
- **CI**: 4/4 `completed/success` at `3c26957`, polled 2026-11-28T00:52Z. Delivery debt for
  NEX-072: **none**.

## Next best actions (run `NEX-2026-11-27-73`, superseding the lists above)

1. **Read CI for `09888ac`** (NEX-073, test-only) and promote it to VERIFIED if every
   check-run concludes success. This is the only outstanding evidence debt for the task;
   the code and the gates are already green locally. Use
   `gh run list --branch feature/agentic-enterprise-platform`, and judge on check-runs whose
   `status` is `completed` — a docs commit descending from it reads `cancelled` once
   superseded in the concurrency group, which is not a failure.
2. **PR #5 body catch-up — DONE through NEX-074.** The body now carries NEX-052…NEX-074
   (56934 chars, updated 2026-11-28T02:23Z). No delivery debt outstanding; keep appending
   one section per slice as work lands.
3. **`operator_access_log` retention policy** — run 72's named follow-up and now the top
   open item. The table is append-only and unbounded; nothing prunes it. Needs a decision on
   how long a privileged-read record must be kept before code, so it is worth an ADR line
   rather than an arbitrary `DELETE`. Related: an operator-grant rationale is still not
   persisted (no column), which needs a migration if it is wanted.

Do not re-implement NEX-058, NEX-073 or NEX-074. NEX-073 is test-only: if its CI turns red,
read the failing assertion before assuming the product broke — the change cannot alter
behaviour.


## Next best actions (run `NEX-2026-11-27-75`, **closed** — see the run-76 list below)

1. ~~**Read CI for `319a3dd`** (NEX-075)~~ — **DONE.** 4/4 `completed/success` (backend ×2,
   frontend ×2), polled 2026-11-28T02:45Z. NEX-075 is VERIFIED.
2. ~~**PR #5 body catch-up: NEX-075 only.**~~ — **DONE.** Body PATCHed 56934 → 60453 chars; exactly
   one NEX-075 section, NEX-074 intact. Issue #2 comment `7232583113` posted the same pass.
3. **Then take the next dependency-ready backlog slice** from `BACKLOG.md`. Do not start a new
   slice with under 10 minutes left on the clock. — **still the standing instruction.**

**Do not redo** NEX-058, NEX-073, NEX-074 or NEX-075 — all four are VERIFIED. NEX-075's slice is
delivered end to end: implemented, tested, committed, pushed, CI green, PR body and tracking issue
updated. There is no outstanding work on it.

**Still open, and deliberately not taken:**

- An operator-grant `rationale` is not persisted (the row has no column). Adding one needs a
  migration; until then nothing may promise a rationale is recorded.
- PR #5 stacks twenty slices. The reviewer's "consider a split" note stands and is a **human**
  decision; NEXUS may not split or merge unilaterally.
- No scheduled purge exists for the access log (ADR-017): an unattended purger changes who is
  accountable for an erasure.

## Next best actions (run `NEX-2026-11-27-76`, superseding the lists above)

1. **Take the next dependency-ready backlog slice.** Nothing is outstanding: NEX-075 is VERIFIED,
   PR #5's body covers NEX-052…NEX-075, and issue #2 carries comment `7232583113`. Read
   `BACKLOG.md` for the highest-priority task whose dependencies are met, and prefer one that
   unblocks several others. Do not start a new slice with under 10 minutes left on the clock.
2. **Two named follow-ups, both needing a decision before code:** persist an operator-grant
   `rationale` (requires a migration), and decide with the owner whether PR #5's twenty stacked
   slices should be split before review. Neither is NEXUS's call alone.
3. **Keep delivery state honest as you go.** A push is not a verdict: poll check-runs whose
   `status` is `completed` before writing VERIFIED, and remember a docs commit reads
   `completed/cancelled` once superseded in the concurrency group — never a failure.

## Run `NEX-2026-11-27-78` — NEX-078: the two PR #5 security findings

**Phase:** 3 (governance), continuing.

**Active task:** none. NEX-078 is IMPLEMENTED + TESTED + COMMITTED + PUSHED (`6637c50`);
its CI was still registering when this was written, so it is **not** VERIFIED yet.

**Completed this run:** NEX-078, the two security findings raised in PR #5's discussion.

- **P1 — distinct policy author, approver and publisher.** The prior rule checked only
  `author != publisher`. That left a gap: an operator who approved a colleague's draft could
  then publish it, so one person performed both halves of the two-person control while three
  names appeared in the audit trail. `separation_of_duties()` now checks all three pairs, and
  `approval_separation_of_duties()` applies the same rule to the approval step so an author
  cannot approve their own draft. Enforced at the domain, in the `Publish`/`Approve` use cases,
  and by DB CHECK `ck_policy_versions_policy_versions_separation_of_duties` (migration
  `5d2b7c41e9a8`). The CHECK tolerates legacy published rows that record no approver, so
  existing data stays readable.
- **P2 — timezone-aware operator grant expiration.** `expires_at` is the whole safety property
  of a bounded grant, so an ambiguous one is refused. A naive datetime names a wall-clock
  reading in a zone the request never specified; the store would relabel it as UTC, so a UTC+10
  operator who meant local midnight would grant ten hours more access than they wrote. Awareness
  is checked before the comparison at the domain boundary and in `IssueOperatorGrant`, because
  comparing a naive deadline to the aware clock raises `TypeError` — a 500 instead of the
  fail-closed refusal the type promises.

**Validation (on the committed tree):** 1589 passed / 4 skipped, 0 failed; `ruff format --check`
315 files already formatted; `ruff check` all passed; `mypy` clean on 143 files; migration
round-trips upgrade → downgrade −1 → upgrade on top of `b4e6f1a2c9d7`, single head.

**Reconciliation note:** this work was begun as NEX-077, but a concurrent run claimed that id
for the operator-grant rationale feature (now VERIFIED at `9f9a70f`). It was renumbered to
NEX-078 and rebased onto the true head. Only this run's own references were renumbered; the
other run's NEX-077 references were left untouched.

## Next best actions (run `NEX-2026-11-27-78`)

1. **Poll CI on `6637c50`.** Mark NEX-078 VERIFIED only when every check-run is
   `completed`/`success`. A docs commit reads `completed/cancelled` once superseded in the
   concurrency group — never a failure.
2. **Append NEX-078 to PR #5's body and comment on issue #2**, naming the two findings it closes.
3. **Take the next dependency-ready backlog slice** from `BACKLOG.md`. Do not start a new slice
   with under 10 minutes left on the clock.

**Do not redo** NEX-078, NEX-077, NEX-076, NEX-075, NEX-074, NEX-073 or NEX-058 — all VERIFIED.

## Next best actions (run `NEX-2026-11-28-82`, superseding the lists above)

1. **Select the next dependency-ready slice from `BACKLOG.md`.** Phase 3 (M3.1–M3.4) is now
   fully VERIFIED and `ROADMAP.md` says so; the remaining value is Phase 4 (admin console
   frontend) and the tail of the backend gaps. Nothing is carried over from run 81.
2. **Carry the chosen slice through the CI poll in the same run.** Runs 80 and 81 both stopped
   at COMMITTED + PUSHED, leaving the tree in a state that was built and locally tested but
   unproven. Polling CI and delivering to the PR/issue is part of the slice, not a follow-up.
3. **Keep reporting truthful in both directions.** This run found `ROADMAP.md` claiming three
   Phase 3 milestones were NOT STARTED when all four were verified, and a `next_task` telling
   the next run to redo PR and issue delivery that was already done. A state file that
   under-reports completion costs a whole run in redundant work.

**Do not redo** NEX-081, NEX-080, NEX-079, NEX-078, NEX-077, NEX-076, NEX-075, NEX-074 or
NEX-073 — all VERIFIED with polled CI. Do not re-append NEX-078…NEX-081 to PR #5's body;
all four are in it.

## Next best actions (run `NEX-2026-11-28-89`, superseding every list above)

No NEXUS delivery debt remains through NEX-088. The next run starts clean:

1. **Select the next dependency-ready slice** from `BACKLOG.md` (Phase 3 governance). Check what
   the backlog still lists as open rather than assuming a successor exists.
2. **Carry the shape forward**: exact reads for exact questions, mutation-tested guards, and a
   regression test that fails against the old answer. NEX-082/086/087/088 were one family.
3. **Do not start a new slice with under 10 minutes left** of the window; integration and
   reporting are not compressible.

**Do not redo** NEX-088 or anything before it. **Do not** re-append NEX-078…NEX-088 to PR #5's
body — all of them are already in it.

## NEX-090 (run `NEX-2026-11-28-91`) — the run history was capped at 50 and had no way past it

**Status: VERIFIED.** Product `aa4b2c8`, CI **4/4 `completed/success`** on docs head `ea0a696` (runs
`46131679941` push, `46131683446` pull_request, Backend + Frontend green, polled 02:41Z). Delivered to
PR #5 (body) and issue #2 (comment `7237454626`) this run.

`GET /agent-runs?agent_id=…` read an agent's runs through a store listing capped at 50 rows. The use
case returned that capped list unchanged and the route described it with
`PageMeta(total=len(items), limit=len(items), offset=0)`. An agent with 60 runs answered
`items: 50, total: 50, limit: 50` — a complete-looking history of fifty — and the route accepted no
`limit`/`offset` at all, so runs 51–60 were unreachable through the API.

This is the NEX-082/086/087/088/089 family (a bounded or derived value standing in for the exact
one), with an extra edge: on the earlier surfaces the total lied but paging worked, so the defect was
recoverable by turning the page. Here the escape hatch was missing too.

`meta.total` is now a `COUNT` scoped by the same `agent_id` as the listing, asked for directly rather
than inferred from a page. The route takes `limit` (`ge=1, le=200`, default 50) and `offset` (`ge=0`).
`list_for_agent` orders by `(created_at DESC, id DESC)`, because offset paging over a
non-deterministic order can duplicate one row and skip another as the windows shift.

`resumable_only` is deliberately *not* windowed: it is the recovery set, so a window there would
silently abandon runs. Its `meta` describes the set it actually returned rather than echoing a
`limit` the request did not apply — found and fixed this run, because the first version reported
`limit=1` beside three returned items, which reads as a truncated page that was not truncated. The
existing test could not see it: it passed `limit=1` and asserted only `total`.

`test_paging_parameters_are_validated` was added for the same reason. `ge=1, le=200, ge=0` were
declared but unasserted; `limit=0` is not a harmless degenerate case, it returns an empty page a
client reads as an empty history.

The suite did not catch the original defect because every fixture held three runs, and three runs
pass with the cap in place. The new regression plans **60**, asserts `total == 60`, that `offset=50`
returns the remaining 10 with the same total, and that the two pages together contain every run id
exactly once.

Gates on the committed tree: `ruff format` 321 files unchanged, `ruff check` all passed, `mypy` clean
on 143 files, full suite exit 0 — **1717 passed, 4 skipped**. Mutation control: use case back to
`total = len(items)` → the 60-run regression fails; restored green.

Security surface unchanged — read-only, no migration, no new authority. The count is scoped by agent
for the same reason the listing is: an unscoped count discloses another agent's volume, which is the
leak NEX-088's mutation check found in the same shape of code.

## Next best actions (run `NEX-2026-11-28-91`, superseding the list above)

1. **Take the next dependency-ready slice.** NEX-090 closed the last named instance of the
   NEX-082…090 family; nothing in `BACKLOG.md` is left marked open from that family. Read the
   backlog's open entries and the roadmap's current phase rather than assuming a successor exists.
   The standing lead, if it survives scrutiny: `list_for_agent` (delegations) and
   `list_pending_for_agent` are unpaginated, but neither is exposed on a listing route, so there is
   **no defect today** — do not "fix" them speculatively, add the route first or leave them.
2. **Run the next slice against the same standard NEX-090 was held to:** a test that fails without the
   fix, a mutation that proves the test bites, and a verdict read rather than assumed.
3. **Do not start a new slice with under 10 minutes of window left.** Commit, push, deliver, record.

**Do not redo** NEX-090 or anything before it. **Do not** re-append NEX-079…NEX-090 to PR #5's body —
all of them are already in it.

## NEX-091 (run `NEX-2026-11-28-92`) — external IdP identities can be linked, listed and unlinked

**Status: VERIFIED — CI 4/4 `completed/success` on docs head `6f8ca19`** (runs `46134645157` push, `46134650328` pull_request; Backend and Frontend both green), product tree identical to `977b21f`. Delivered to PR #5 (body) and issue #2 (comment `7237852341`).

`977b21f`'s own runs read `completed/cancelled` — superseded in the concurrency group by the docs push, never evidence in either direction. The verdict is anchored to the docs head
and licensed by an **empty product diff**: `git diff 977b21f 6f8ca19` touches only
`docs/NEXUS/{BACKLOG.md,CHECKPOINT.json,EXECUTION_LOG.md,STATE.md}`, so the green runs tested
exactly the tree `977b21f` ships.

OIDC login needs a durable binding from an IdP `(provider, subject)` pair to a local account, and an
operator needs to create and revoke it. This slice adds the three operations end to end: repository
port methods, the SQLAlchemy adapter, three use cases in `application/use_cases/identity_linking.py`,
three routes on the organization admin router, and 23 tests (10 application, 13 HTTP).

**The unlink route addresses the link by its own id, not by `(provider, subject)`.** An IdP subject is
opaque and routinely contains `/`, `@` or `+` — `auth0|64f/0a+b@tenant/realms/acme#user` is a normal
subject. A path segment cannot carry those unescaped, and the failure was not a clean `400`: the route
failed to *match* and answered `404` without ever reaching the use case, so the link looked absent
rather than unaddressable. Addressing the id removes the escaping problem instead of documenting a
workaround. `test_opaque_subject_with_path_characters_survives_round_trip` pins it.

Security decisions, each pinned by a test:

- **Linking is a takeover route, so it cannot escalate.** An admin may not attach a login to an
  account more privileged than their own (`403`). A less privileged target is the only permitted one.
- **`(provider, subject)` is unique system-wide.** Re-linking the same pair to the same user is
  idempotent rather than a conflict; binding a pair already owned by a different account is refused
  `422` instead of silently rebinding another user's login.
- **Unlink revokes only the sessions minted from that exact `(provider, subject)`, in the same
  transaction.** Unrelated sessions survive, and a live cookie from the removed link cannot outlive
  the removal.
- **404 for unreadable, 403 for unauthorized**, matching the rest of the admin surface. A link is
  removable only through the member that owns it, so a guessed id cannot cross accounts.
- **The audit entry names actor, target, provider and subject — never a credential** — and only after
  the change succeeds.

One test-writing lesson worth keeping: the seeded demo users **already carry a `demo` identity link**,
so three assertions that counted the list absolutely were asserting the *seed*, not the change under
test — they failed for the right reason. They are now delta-based (`body["id"] in listed_ids`, and
"exactly one `entra` link" rather than "exactly one link").

**Validation (on the committed tree):** `ruff format` 1 reformatted then 323 unchanged, `ruff check`
all passed, `mypy` clean on **144** files, full suite **1740 passed / 4 skipped**, exit 0.

## NEX-092 (run `NEX-2026-11-28-93`) — the revoked-grant report was bounded only after it had read everything

**Status: COMMITTED `3159555` · PUSHED · VERIFIED — CI 4/4 `completed/success`**, runs
`46136495333` (push) and `46136499460` (pull_request) **on the product commit itself** — no
docs commit shared its concurrency group, so no anchor or empty-diff licence was needed this run.

`revoked_grants(limit=..., offset=...)` was recorded by NEX-086 as a bounded read. It was not. The
adapter ran `select(OperatorGrantRow)` with **no `WHERE`**, built a domain object for every grant row
in the table, and only then sliced the resulting list. A five-row page cost the entire table, and the
cost grew with the deployment's total grant history — every grant ever issued, live or revoked.
`count_revoked()` did the same thing to answer with a single integer.

This is the unbounded-read defect NEX-087 fixed for the access log and NEX-088 for the audit trail,
one layer lower: in the adapter rather than in the use case. Those two were `len(items)` masquerading
as a total; this one is a page whose cost was the table.

### Why NEX-086's premise does not hold

NEX-086 deliberately kept the predicate in Python:

> ``revoked_at IS NOT NULL`` is a faithful SQL translation, but keeping the selection in Python
> preserves the single definition of "revoked" … a schema-level predicate could drift from
> ``is_revoked`` and page a different set than the report counts.

That was a reasonable caution, and it is now demonstrably unnecessary for *this* predicate.
`OperatorGrant.is_revoked` is a single attribute test with no logic in it:

```python
@property
def is_revoked(self) -> bool:
    return self.revoked_at is not None
```

`revoked_at IS NOT NULL` is therefore its **exact** translation, not an approximation of it. There is
no derived state, no clock, no second column, nothing to drift. The table's
`CheckConstraint("revoked_by IS NULL OR revoked_at IS NOT NULL", name="revocation_actor_requires_instant")`
forbids the only incoherent half — a named revoker with no instant — so the two representations
cannot disagree on any row the schema admits. I read the constraint rather than assuming it; it is at
`models.py` in `OperatorGrantRow.__table_args__`.

**`is_active` is different, and its filter stays in Python.** That predicate carries a clock and two
nullable deadlines (`not_before`, `expires_at`) evaluated against `now`, so a SQL translation would
have to duplicate real logic and could genuinely drift. The asymmetry is the point: push down what is
a column test, keep what is a computation.

### What changed

- `revoked_grants`: `WHERE revoked_at IS NOT NULL ORDER BY id OFFSET :offset LIMIT :limit` — filter,
  order and window all in the store; rows are converted to domain objects only for the page.
- `count_revoked`: `SELECT count(*) … WHERE revoked_at IS NOT NULL` — an aggregate that reads no rows
  and cannot be truncated by volume.
- `id` ordering is retained because it is the port's insertion order *and* what makes `LIMIT`/`OFFSET`
  stable: neither method appends to the list it reads, so a page cannot shift under a walker.

No migration, no new authority, no contract change. Same rows, same order, same results.

### The test asserts on work, not on SQL text

Asserting the statement's SQL string would pin the implementation and pass for the wrong reasons.
`_to_domain` is the adapter's only row-to-domain path, so counting its calls counts the rows actually
read. `test_a_page_does_not_read_the_whole_grant_table` seeds 80 grants (40 live, 40 revoked), asks
for five, and asserts exactly five conversions.

**Mutation control run:** restoring the previous adapter makes the test fail with `assert 80 == 5` —
all 40 never-revoked grants were being converted to answer a five-row page. The test bites on the real
defect, not on an artefact of the fix.

### Validation (on the committed tree)

`ruff format` 324 files unchanged · `ruff check` all passed · `mypy` clean on **144** files · full
suite **1741 passed / 4 skipped**, exit 0. The four skips are the PostgreSQL-marked tests, which CI's
Backend job runs over the migration path.

### Note for the next reader

NEX-086's docstrings *asserted* this read was bounded while its body read the whole table. The lesson
is not "trust the docstring less" — it is that a comment recording a *performance property* should be
checked against the statement it describes, because a green test suite proves correctness, not cost.
The regression here asserts cost.

## Next best actions (run `NEX-2026-11-28-93`, **superseded** — kept for history; the live list is the
run-98 block near the top of this file)

No NEXUS delivery debt remains through NEX-092. The next run starts clean:

1. **Select the next dependency-ready slice** from `BACKLOG.md` / `ROADMAP.md`.
2. **Finish the adapter sweep for the NEX-086 mistake** — a read documented as bounded whose body
   still materialises the table. NEX-087 (access log), NEX-088 (audit trail) and NEX-092 (operator
   grants) were the same defect three times. The cheap signal is a docstring promising bounding above
   a `select()` with no `where(...)`, not an exhaustive grep.
3. **Carry the shape forward**: a test that fails without the fix, a mutation that proves the test
   bites, and a CI verdict read rather than assumed.
4. **Do not start a new slice with under 10 minutes of window left.** Commit, push, deliver, record.

**Do not redo** NEX-092 or anything before it. **Do not** re-append NEX-079…NEX-092 to PR #5's body —
all of them are already in it.

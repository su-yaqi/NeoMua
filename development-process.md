# NeoMua Development Process

This document defines how a change moves from a task to a traceable, reviewable result. Repository-wide agent rules are in [AGENTS.md](./AGENTS.md); local environment commands are in [development-workflow.md](./development-workflow.md).

## 1. Start from verified facts

Before editing:

1. Read `AGENTS.md` and any more specific module instructions.
2. Check the current branch, worktree changes, task goal, affected tests, context, and deployment implications.
3. Continue the current branch only when its goal matches the task. Otherwise create a short `feat/*`, `fix/*`, or `chore/*` branch from the verified starting point.
4. Declare change risk S, M, or H before implementation. When uncertain, choose the higher level.

The default branch must remain releasable. Versions are represented by tags and GitHub Releases, not new long-lived version branches.

## 2. S/M/H validation matrix

| Risk | Typical scope | Required validation | Additional gates |
|---|---|---|---|
| S | Copy, comments, formatting, test descriptions, behavior-equivalent cleanup | Relevant formatting/static checks and the smallest test covering the change | Explain why application tests are not applicable when omitted |
| M | Single-module feature or bug fix, ordinary API/UI behavior, developer tooling/process | Affected unit/integration/E2E tests; generated client validation when API changes | Record scope boundaries and context impact |
| H | Auth, permissions, database/migration, dependencies/locks, deployment, runtime topology, cross-module architecture | Full relevant suite and repository-level checks | Migration/data validation, rollback/recovery validation, staging health and critical smoke evidence |

Path-based automation only establishes a minimum. A small diff can still be H because of what it changes.

An H change is not complete when staging, rollback, migration, secrets, runner access, or other required infrastructure is unavailable. Prepare the code and configuration checklist, report the blocker, and stop.

## 3. Context impact

Every change records one of:

- `none`: no semantic capability or current-fact change; include a reason.
- `updated`: current behavior, interface, flow, UI, data, or architecture facts changed; list the context files.

Do not update context only because files changed. Tests, formatting, internal refactors, and equivalent implementations usually have `context impact: none`.

When facts change, follow [context/readme.md](./context/readme.md):

- root files for global current facts;
- `context/modules/` for module API, flow, and UI facts;
- `context/prds/` and `context/changelogs/` for version requirements and snapshots.

Only implemented and verified facts belong in current context.

## 4. Process check

After committing a coherent change, run:

```bash
CHANGE_BASE_REF=master \
CHANGE_RISK=M \
CHANGE_CONTEXT_IMPACT=updated \
CHANGE_CONTEXT_NOTE="Added repository development governance" \
CHANGE_TEST_EVIDENCE="process checker fixtures and repository file hooks passed" \
make check-change
```

For a stacked short branch, set `CHANGE_BASE_REF` to its verified parent branch instead of `master`.

H changes additionally require:

```bash
CHANGE_FULL_TEST_EVIDENCE="..." \
CHANGE_MIGRATION_EVIDENCE="..." \
CHANGE_ROLLBACK_EVIDENCE="..." \
CHANGE_STAGING_EVIDENCE="..."
```

The checker:

- calculates a minimum risk from changed paths;
- rejects a declared risk below that minimum;
- verifies test and context-impact evidence;
- requires completed H-level evidence;
- confirms that `context impact: updated` corresponds to changed files under `context/`.

It cannot infer semantic risk, so authors and reviewers must raise the level when required.

## 5. Pull request and completion

Use `.github/PULL_REQUEST_TEMPLATE.md` and record:

- goal and out-of-scope boundaries;
- one S/M/H level and rationale;
- actual test results;
- context impact;
- H-only migration, rollback, and staging evidence;
- branch, commits, remote state, and deployment/release state.

A task is complete only when scope is contained, risk-matched validation is recorded, context impact is handled, logical commits exist, status is traceable, and remaining risks are explicit.

When remote writes are authorized, push the first stable commit and again at the end of work. Without authorization, keep commits local and report that state. Delete a short branch only after its merge is confirmed and deletion is authorized.

# NeoMua Graded CI

NeoMua uses one top-level GitHub Actions workflow, `CI`, to plan checks and publish one branch-protection result named `CI Gate`. The development rules and S/M/H definitions remain authoritative in [development-process.md](./development-process.md).

## Pull request planning

The CI planner reads both the changed paths and the declarations in `.github/PULL_REQUEST_TEMPLATE.md`.

| Declared risk | CI behavior |
|---|---|
| S | Always runs changed-range hygiene. Test-only changes run their corresponding suite; documentation/process-only changes do not start unrelated E2E. |
| M | Runs hygiene plus backend, E2E, Compose, integrity, or workflow-security jobs selected from affected paths. |
| H | Runs all reusable checks and requires completed full-test, migration/data, rollback/recovery, and staging evidence in the PR body. |

Paths establish a minimum risk. The planner rejects an understated declaration, a missing or ambiguous risk/context selection, or incomplete H evidence. Semantic risk still wins when the path alone looks harmless.

The workflow itself has no `paths` filter. This ensures every PR gets a conclusive `CI Gate`; unplanned jobs are skipped inside the workflow and the gate verifies that only expected skips occurred.

## Full-run events

The following events run every reusable check:

- push to `master`;
- the nightly scheduled run;
- a published GitHub Release;
- manual `workflow_dispatch`.

Full runs cover repository lint/type checks, backend coverage, Playwright, local and production Compose rendering, generated OpenAPI client drift, Python and Bun lock files, Alembic migration heads, and GitHub Actions security.

## Local control tests

Run the planner and gate fixtures without GitHub or Docker:

```bash
make test-ci-control
```

Validate migration upgrade and the current Alembic head in an isolated Compose project:

```bash
make DEV_SLOT=0 test-migrations
```

Playwright arguments can be forwarded without bypassing the isolated project:

```bash
make DEV_SLOT=0 test-e2e ARGS="--grep 'critical flow'"
```

## Deployment dependency

The staging and production workflows now listen for a completed `CI` workflow and proceed only after a successful conclusion:

- staging accepts only a successful `push` run for `master`;
- production accepts only a successful `release` run;
- both check out the exact successful `head_sha`.

This is only the Phase 3 dependency gate. Deployment still builds on the self-hosted runner and starts Compose in place. Immutable images, build-once promotion, backup/migration sequencing, health gates, and rollback belong to Phase 4 and are not implemented here.

## GitHub activation checklist

Repository files alone cannot enable enforcement. Before calling this CI active:

1. Push the workflow commit only after authorization and merge it through the approved path.
2. Confirm GitHub registers `CI` and complete the first full run.
3. Resolve any real full-suite failures; do not lower gates to make the first run green.
4. Protect `master` and require the single status check `CI Gate`.
5. Confirm skipped S checks still leave `CI Gate` successful and a failed planned job makes it fail.
6. Keep deployment environments and runners disabled until the Phase 4 configuration checklist is approved.

As of the 2026-07-29 external audit, GitHub had no registered workflows or runs, no branch protection or rulesets, no Environments, and no self-hosted runners. The local workflow implementation therefore remains unactivated until the steps above are completed.

Local Phase 3 verification passed the CI control fixtures, process-check fixtures, YAML/Shell/Python syntax, Ruff for the new controls, zizmor, Compose rendering, Alembic upgrade/head checks, and all 74 Playwright tests. All 218 backend tests also passed, but measured coverage was 60%, so the preserved 90% coverage gate failed. The earlier full-repository baseline also contains unresolved mypy/ty findings. These are real blockers to a successful first full `CI` run; they must be resolved explicitly rather than hidden by lowering or skipping gates.

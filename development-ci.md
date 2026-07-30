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

Full runs cover repository lint/type checks, backend tests and a non-blocking coverage report, Playwright, local and production Compose rendering, generated OpenAPI client drift, Python and Bun lock files, Alembic migration heads, and GitHub Actions security.

The repository-wide backend coverage threshold is temporarily disabled by explicit user approval on 2026-07-29. Tests still collect and upload coverage, but neither the backend job nor Smokeshow fails on the percentage. Restoring a ratcheted or absolute threshold is separate follow-up work; this exception must not be described as 90% coverage passing.

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

## GitHub activation status

The CI control plane was activated on 2026-07-30:

1. The authorized public branch is attached to [draft PR #1](https://github.com/su-yaqi/NeoMua/pull/1), and GitHub has registered the `CI` workflow.
2. Full run [30508474040](https://github.com/su-yaqi/NeoMua/actions/runs/30508474040) passed on exact commit `c7b97c7ea791fc67b37f8e7f1c7e049eb75693c5`, including all reusable jobs and the final `CI Gate`.
3. `master` branch protection is strict and requires only `CI Gate`; it applies to administrators, while force pushes and branch deletion remain disabled.
4. The planner/gate fixtures confirm that unplanned jobs may be skipped, while any failed planned job fails `CI Gate`.

Draft PR #1 remains intentionally blocked. It is an H change and does not claim staging evidence: the repository had zero GitHub Environments and zero self-hosted runners when rechecked on 2026-07-30. Do not mark the staging checkbox complete, make the PR ready, or merge it until an authorized staging environment runs the required health and smoke checks.

To unblock that evidence through the current Phase 3 workflow, an administrator must:

1. Create the `staging` GitHub Environment and configure its protection/approval policy.
2. Register a trusted runner with both `self-hosted` and `staging` labels.
3. Configure the Environment secrets currently referenced by `deploy-staging.yml`: `DOMAIN_STAGING`, `STACK_NAME_STAGING`, `SECRET_KEY`, `INTERNAL_RUNTIME_TOKEN`, `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAILS_FROM_EMAIL`, `POSTGRES_PASSWORD`, and optional `SENTRY_DSN`.
4. Verify the staging host has the supported Docker/Compose runtime and access to the target domain/network.
5. After an authorized `master` update with a successful exact-SHA `CI Gate`, record the staging deployment run, service health result, and critical smoke evidence. A successful Compose start alone is not sufficient.

This checklist only describes the current Phase 3 prerequisite. Do not activate this legacy build-in-place deployment as the final production design: Phase 4 must first replace it with immutable build-once images, explicit migration/backup ordering, health gates, and a tested rollback path.

Local Phase 3 verification passed the CI control fixtures, process-check fixtures, YAML/Shell/Python syntax, Ruff, mypy, ty, zizmor, Compose rendering, Alembic upgrade/head checks, generated-client drift checks, the frontend production build, all 255 backend tests, and all 74 Playwright tests. The remote full run repeated the complete graded jobs successfully. Client generation explicitly enables the local-only private test API needed by Playwright without changing its disabled runtime/deployment default. Backend coverage remains 60% and is reported without gating under the approved temporary exception.

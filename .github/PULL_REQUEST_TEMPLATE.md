## Scope

<!-- Describe the single goal of this PR and explicitly state what is out of scope. -->

Goal:

Out of scope:

## Change risk

<!-- Select exactly one. Path checks provide only a minimum; semantic risk wins. -->

- [ ] S — low-risk adjustment with no product, data, permission, or topology change
- [ ] M — normal feature, bug fix, or local behavior/process change
- [ ] H — auth, permissions, database/migration, dependencies, deployment, runtime topology, or cross-module architecture

Risk rationale:

## Validation

<!-- List commands/checks and their actual results. Explain any intentionally omitted test. -->

- Test evidence:

### H-only gates

<!-- H changes cannot be complete while any applicable item is pending. -->

- [ ] Full relevant test suite passed
- [ ] Migration or data-change validation completed, or a concrete non-applicability reason recorded
- [ ] Rollback/recovery validation completed
- [ ] Staging health check and critical smoke flow passed

Evidence:

- Full tests:
- Migration/data:
- Rollback/recovery:
- Staging:

## Context impact

<!-- Select exactly one. Do not update context mechanically. -->

- [ ] `none` — no semantic capability/current-fact change
- [ ] `updated` — semantic capability/current facts changed

Reason:

Updated context files:

## Traceability

- Branch:
- Logical commits:
- Remote status:
- Deployment/release status:

## Completion

- [ ] Scope is contained and unrelated changes are excluded
- [ ] Validation matches the declared risk
- [ ] Context impact is recorded and accurate
- [ ] Remaining risks and external configuration are documented
- [ ] No push, merge, release, or deployment was performed without authorization

#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
checker="${repo_root}/scripts/check-change-process.sh"
fixture_root="$(mktemp -d)"

cleanup() {
    rm -rf "${fixture_root}"
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

write_fixture() {
    local name="$1"
    shift
    printf '%s\n' "$@" >"${fixture_root}/${name}"
}

expect_pass() {
    local name="$1"
    shift
    if ! "$@" >"${fixture_root}/${name}.out" 2>&1; then
        sed -n '1,160p' "${fixture_root}/${name}.out" >&2
        fail "${name} should pass"
    fi
}

expect_fail() {
    local name="$1"
    shift
    if "$@" >"${fixture_root}/${name}.out" 2>&1; then
        sed -n '1,160p' "${fixture_root}/${name}.out" >&2
        fail "${name} should fail"
    fi
}

write_fixture s-files README.md
write_fixture m-files AGENTS.md context/readme.md
write_fixture h-files .github/workflows/example.yml

common_env=(
    CHANGE_CONTEXT_NOTE="fixture context decision"
    CHANGE_TEST_EVIDENCE="fixture validation passed"
)

expect_pass s-docs \
    env \
    CHANGE_FILES_FILE="${fixture_root}/s-files" \
    CHANGE_RISK=S \
    CHANGE_CONTEXT_IMPACT=none \
    "${common_env[@]}" \
    "${checker}"

expect_fail s-cannot-understate-m \
    env \
    CHANGE_FILES_FILE="${fixture_root}/m-files" \
    CHANGE_RISK=S \
    CHANGE_CONTEXT_IMPACT=updated \
    "${common_env[@]}" \
    "${checker}"

expect_fail context-updated-needs-file \
    env \
    CHANGE_FILES_FILE="${fixture_root}/s-files" \
    CHANGE_RISK=S \
    CHANGE_CONTEXT_IMPACT=updated \
    "${common_env[@]}" \
    "${checker}"

expect_pass m-process-with-context \
    env \
    CHANGE_FILES_FILE="${fixture_root}/m-files" \
    CHANGE_RISK=M \
    CHANGE_CONTEXT_IMPACT=updated \
    "${common_env[@]}" \
    "${checker}"

expect_fail h-cannot-understate \
    env \
    CHANGE_FILES_FILE="${fixture_root}/h-files" \
    CHANGE_RISK=M \
    CHANGE_CONTEXT_IMPACT=none \
    "${common_env[@]}" \
    "${checker}"

expect_fail h-requires-evidence \
    env \
    CHANGE_FILES_FILE="${fixture_root}/h-files" \
    CHANGE_RISK=H \
    CHANGE_CONTEXT_IMPACT=none \
    "${common_env[@]}" \
    "${checker}"

expect_fail h-rejects-pending-staging \
    env \
    CHANGE_FILES_FILE="${fixture_root}/h-files" \
    CHANGE_RISK=H \
    CHANGE_CONTEXT_IMPACT=none \
    CHANGE_FULL_TEST_EVIDENCE="full suite passed" \
    CHANGE_MIGRATION_EVIDENCE="not applicable: no data change" \
    CHANGE_ROLLBACK_EVIDENCE="revert procedure verified" \
    CHANGE_STAGING_EVIDENCE="pending access" \
    "${common_env[@]}" \
    "${checker}"

expect_fail h-rejects-failed-suite \
    env \
    CHANGE_FILES_FILE="${fixture_root}/h-files" \
    CHANGE_RISK=H \
    CHANGE_CONTEXT_IMPACT=none \
    CHANGE_FULL_TEST_EVIDENCE="218 tests passed but coverage gate failed" \
    CHANGE_MIGRATION_EVIDENCE="not applicable: no data change" \
    CHANGE_ROLLBACK_EVIDENCE="revert procedure verified" \
    CHANGE_STAGING_EVIDENCE="health and smoke checks passed" \
    "${common_env[@]}" \
    "${checker}"

expect_pass h-complete \
    env \
    CHANGE_FILES_FILE="${fixture_root}/h-files" \
    CHANGE_RISK=H \
    CHANGE_CONTEXT_IMPACT=none \
    CHANGE_FULL_TEST_EVIDENCE="full suite passed" \
    CHANGE_MIGRATION_EVIDENCE="not applicable: no data change" \
    CHANGE_ROLLBACK_EVIDENCE="revert procedure verified" \
    CHANGE_STAGING_EVIDENCE="health and smoke checks passed" \
    "${common_env[@]}" \
    "${checker}"

printf 'PASS: change process checker fixtures\n'

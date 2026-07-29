#!/usr/bin/env bash

set -Eeuo pipefail

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  CHANGE_RISK=S|M|H \
  CHANGE_CONTEXT_IMPACT=none|updated \
  CHANGE_CONTEXT_NOTE="reason" \
  CHANGE_TEST_EVIDENCE="actual result" \
  [CHANGE_BASE_REF=origin/master] \
  [CHANGE_HEAD_REF=HEAD] \
  [CHANGE_FILES_FILE=/path/to/files.txt] \
  [CHANGE_FULL_TEST_EVIDENCE="..."] \
  [CHANGE_MIGRATION_EVIDENCE="..."] \
  [CHANGE_ROLLBACK_EVIDENCE="..."] \
  [CHANGE_STAGING_EVIDENCE="..."] \
  scripts/check-change-process.sh

Path checks establish a minimum risk only. Semantic risk can require a higher level.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if (($#)); then
    usage >&2
    fail "unexpected arguments; configure the check with CHANGE_* environment variables"
fi

declared_risk="${CHANGE_RISK:-}"
context_impact="${CHANGE_CONTEXT_IMPACT:-}"
context_note="${CHANGE_CONTEXT_NOTE:-}"
test_evidence="${CHANGE_TEST_EVIDENCE:-}"
base_ref="${CHANGE_BASE_REF:-}"
head_ref="${CHANGE_HEAD_REF:-HEAD}"
files_file="${CHANGE_FILES_FILE:-}"

case "${declared_risk}" in
    S|M|H) ;;
    *) fail "CHANGE_RISK must be exactly S, M, or H" ;;
esac

case "${context_impact}" in
    none|updated) ;;
    *) fail "CHANGE_CONTEXT_IMPACT must be exactly none or updated" ;;
esac

[[ -n "${context_note//[[:space:]]/}" ]] ||
    fail "CHANGE_CONTEXT_NOTE must explain the context impact"
[[ -n "${test_evidence//[[:space:]]/}" ]] ||
    fail "CHANGE_TEST_EVIDENCE must record actual validation or an explicit non-applicability reason"

if [[ -z "${files_file}" && -z "${base_ref}" ]]; then
    if git show-ref --verify --quiet refs/remotes/origin/master; then
        base_ref="origin/master"
    elif git show-ref --verify --quiet refs/heads/master; then
        base_ref="master"
    else
        fail "cannot infer a base ref; set CHANGE_BASE_REF or CHANGE_FILES_FILE"
    fi
fi

tmp_changed="$(mktemp)"
cleanup() {
    rm -f "${tmp_changed}"
}
trap cleanup EXIT

if [[ -n "${files_file}" ]]; then
    [[ -r "${files_file}" ]] || fail "CHANGE_FILES_FILE is not readable: ${files_file}"
    sed '/^[[:space:]]*$/d' "${files_file}" | sort -u >"${tmp_changed}"
else
    git rev-parse --verify "${base_ref}^{commit}" >/dev/null 2>&1 ||
        fail "base ref is not a commit: ${base_ref}"
    git rev-parse --verify "${head_ref}^{commit}" >/dev/null 2>&1 ||
        fail "head ref is not a commit: ${head_ref}"
    git diff --name-only --diff-filter=ACMRTUXB \
        "${base_ref}...${head_ref}" | sort -u >"${tmp_changed}"
fi

[[ -s "${tmp_changed}" ]] || fail "no changed files found for the selected range"

risk_rank() {
    case "$1" in
        S) printf '1\n' ;;
        M) printf '2\n' ;;
        H) printf '3\n' ;;
        *) return 1 ;;
    esac
}

risk_name() {
    case "$1" in
        1) printf 'S\n' ;;
        2) printf 'M\n' ;;
        3) printf 'H\n' ;;
        *) return 1 ;;
    esac
}

minimum_rank=1
minimum_reasons=""
context_changed=0

raise_minimum() {
    local rank="$1"
    local reason="$2"
    if ((rank > minimum_rank)); then
        minimum_rank="${rank}"
    fi
    if [[ -z "${minimum_reasons}" ]]; then
        minimum_reasons="${reason}"
    else
        minimum_reasons="${minimum_reasons}; ${reason}"
    fi
}

while IFS= read -r path; do
    [[ -n "${path}" ]] || continue

    case "${path}" in
        context/*)
            context_changed=1
            ;;
    esac

    case "${path}" in
        .github/workflows/*|.github/actions/*|\
        compose.yml|compose.*.yml|\
        Dockerfile|*/Dockerfile|*/Dockerfile.*|\
        scripts/deploy*|scripts/*deploy*|\
        deployment.md|\
        pyproject.toml|*/pyproject.toml|uv.lock|\
        package.json|*/package.json|bun.lock|\
        .github/dependabot.yml|.pre-commit-config.yaml|\
        backend/alembic.ini|backend/app/alembic/*|\
        backend/app/models.py|backend/app/*models.py|\
        backend/app/auth_sessions.py|backend/app/csrf.py|\
        backend/app/core/security.py|backend/app/api/deps.py|\
        backend/app/api/routes/login.py|backend/app/api/routes/namespaces.py|\
        frontend/src/hooks/useAuth.ts|frontend/src/lib/browserApi.ts)
            raise_minimum 3 "sensitive path ${path}"
            continue
            ;;
    esac

    case "${path}" in
        AGENTS.md|Makefile|scripts/*|\
        development-process.md|.github/PULL_REQUEST_TEMPLATE.md)
            raise_minimum 2 "repository process path ${path}"
            ;;
        *.md|context/*|\
        backend/tests/*|frontend/tests/*|tests/*|\
        .env.example)
            ;;
        *)
            raise_minimum 2 "implementation/process path ${path}"
            ;;
    esac
done <"${tmp_changed}"

declared_rank="$(risk_rank "${declared_risk}")"
minimum_risk="$(risk_name "${minimum_rank}")"

if ((declared_rank < minimum_rank)); then
    fail "declared risk ${declared_risk} is below path-based minimum ${minimum_risk}: ${minimum_reasons}"
fi

if [[ "${context_impact}" == "updated" && "${context_changed}" -eq 0 ]]; then
    fail "context impact is updated but no context/ file changed"
fi

if [[ "${context_impact}" == "none" && "${context_changed}" -eq 1 ]]; then
    fail "context/ files changed but context impact is none"
fi

evidence_is_complete() {
    local label="$1"
    local value="$2"
    local lowered

    [[ -n "${value//[[:space:]]/}" ]] || fail "${label} is required for H changes"
    lowered="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
    case "${lowered}" in
        *pending*|*"not run"*|*not-run*|*todo*)
            fail "${label} must contain completed evidence, not a pending status"
            ;;
    esac
}

if [[ "${declared_risk}" == "H" ]]; then
    evidence_is_complete "CHANGE_FULL_TEST_EVIDENCE" "${CHANGE_FULL_TEST_EVIDENCE:-}"
    evidence_is_complete "CHANGE_MIGRATION_EVIDENCE" "${CHANGE_MIGRATION_EVIDENCE:-}"
    evidence_is_complete "CHANGE_ROLLBACK_EVIDENCE" "${CHANGE_ROLLBACK_EVIDENCE:-}"
    evidence_is_complete "CHANGE_STAGING_EVIDENCE" "${CHANGE_STAGING_EVIDENCE:-}"
fi

changed_count="$(wc -l <"${tmp_changed}" | tr -d '[:space:]')"

printf 'Change process check passed\n'
printf '  declared risk: %s\n' "${declared_risk}"
printf '  path minimum: %s\n' "${minimum_risk}"
printf '  context impact: %s\n' "${context_impact}"
printf '  changed files: %s\n' "${changed_count}"
if [[ -n "${minimum_reasons}" ]]; then
    printf '  path reasons: %s\n' "${minimum_reasons}"
fi

#!/usr/bin/env bash

set -Eeuo pipefail

NEOMUA_DEV_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
NEOMUA_DEV_SCOPE="${DEV_PROJECT_SCOPE:-neomua}"
NEOMUA_DEV_SLOT="${DEV_SLOT:-0}"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

if [[ ! "${NEOMUA_DEV_SCOPE}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    fail "DEV_PROJECT_SCOPE must match ^[a-z0-9][a-z0-9-]*$ (received: ${NEOMUA_DEV_SCOPE})"
fi

if [[ ! "${NEOMUA_DEV_SLOT}" =~ ^[0-9]+$ ]] || ((10#${NEOMUA_DEV_SLOT} > 99)); then
    fail "DEV_SLOT must be an integer between 0 and 99 (received: ${NEOMUA_DEV_SLOT})"
fi

NEOMUA_DEV_SLOT_NUMBER=$((10#${NEOMUA_DEV_SLOT}))
NEOMUA_DEV_PORT_BASE=$((20000 + NEOMUA_DEV_SLOT_NUMBER * 100))
NEOMUA_DEV_PROJECT_NAME="${NEOMUA_DEV_SCOPE}-dev-${NEOMUA_DEV_SLOT_NUMBER}"
NEOMUA_TEST_PROJECT_NAME="${NEOMUA_DEV_PROJECT_NAME}-test"

export COMPOSE_PROJECT_NAME="${NEOMUA_DEV_PROJECT_NAME}"
export STACK_NAME="${NEOMUA_DEV_PROJECT_NAME}"
export TAG="dev"
export DOCKER_IMAGE_BACKEND="${NEOMUA_DEV_PROJECT_NAME}-backend"
export DOCKER_IMAGE_FRONTEND="${NEOMUA_DEV_PROJECT_NAME}-frontend"
export DEV_FRONTEND_DEV_IMAGE="${NEOMUA_DEV_PROJECT_NAME}-frontend-hot:dev"
export TRAEFIK_CONSTRAINT_LABEL="${NEOMUA_DEV_PROJECT_NAME}-traefik-public"
export TRAEFIK_PUBLIC_NETWORK="${NEOMUA_DEV_PROJECT_NAME}_traefik-public"

export DEV_FRONTEND_PORT="${NEOMUA_DEV_PORT_BASE}"
export DEV_BACKEND_PORT="$((NEOMUA_DEV_PORT_BASE + 1))"
export DEV_MODEL_GATEWAY_PORT="$((NEOMUA_DEV_PORT_BASE + 2))"
export DEV_PROXY_HTTP_PORT="$((NEOMUA_DEV_PORT_BASE + 3))"
export DEV_TRAEFIK_DASHBOARD_PORT="$((NEOMUA_DEV_PORT_BASE + 4))"
export DEV_ADMINER_PORT="$((NEOMUA_DEV_PORT_BASE + 5))"
export DEV_MAILCATCHER_PORT="$((NEOMUA_DEV_PORT_BASE + 6))"
export DEV_SMTP_PORT="$((NEOMUA_DEV_PORT_BASE + 7))"

export DEV_FRONTEND_URL="http://localhost:${DEV_FRONTEND_PORT}"
export DEV_BACKEND_URL="http://localhost:${DEV_BACKEND_PORT}"
export MODEL_GATEWAY_PUBLIC_URL="http://localhost:${DEV_MODEL_GATEWAY_PORT}"
export FRONTEND_HOST="${DEV_FRONTEND_URL}"
export BACKEND_CORS_ORIGINS="${DEV_FRONTEND_URL},http://127.0.0.1:${DEV_FRONTEND_PORT}"

NEOMUA_COMPOSE=(
    docker compose
    -f "${NEOMUA_DEV_ROOT}/compose.yml"
    -f "${NEOMUA_DEV_ROOT}/compose.override.yml"
    --project-name "${NEOMUA_DEV_PROJECT_NAME}"
)

NEOMUA_TEST_COMPOSE=(
    docker compose
    -f "${NEOMUA_DEV_ROOT}/compose.yml"
    -f "${NEOMUA_DEV_ROOT}/compose.override.yml"
    -f "${NEOMUA_DEV_ROOT}/compose.dev-test.yml"
    --project-name "${NEOMUA_TEST_PROJECT_NAME}"
)

require_env_file() {
    if [[ ! -f "${NEOMUA_DEV_ROOT}/.env" ]]; then
        fail "missing ${NEOMUA_DEV_ROOT}/.env; run 'make dev-init' first"
    fi
}

require_docker() {
    command -v docker >/dev/null 2>&1 || fail "docker is not installed or is not on PATH"
    docker info >/dev/null 2>&1 || fail "Docker is not available; start Docker and retry"
}

compose() {
    (
        cd "${NEOMUA_DEV_ROOT}"
        "${NEOMUA_COMPOSE[@]}" "$@"
    )
}

test_compose() {
    (
        cd "${NEOMUA_DEV_ROOT}"
        export COMPOSE_PROJECT_NAME="${NEOMUA_TEST_PROJECT_NAME}"
        export STACK_NAME="${NEOMUA_TEST_PROJECT_NAME}"
        export DOCKER_IMAGE_BACKEND="${NEOMUA_TEST_PROJECT_NAME}-backend"
        export DOCKER_IMAGE_FRONTEND="${NEOMUA_TEST_PROJECT_NAME}-frontend"
        export DEV_FRONTEND_DEV_IMAGE="${NEOMUA_TEST_PROJECT_NAME}-frontend-hot:dev"
        export TRAEFIK_CONSTRAINT_LABEL="${NEOMUA_TEST_PROJECT_NAME}-traefik-public"
        export TRAEFIK_PUBLIC_NETWORK="${NEOMUA_TEST_PROJECT_NAME}_traefik-public"
        "${NEOMUA_TEST_COMPOSE[@]}" "$@"
    )
}

project_container_ids() {
    local project_name="${1:-${NEOMUA_DEV_PROJECT_NAME}}"
    docker ps -aq --filter "label=com.docker.compose.project=${project_name}"
}

container_owner() {
    docker inspect \
        --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        "$1"
}

container_summary() {
    docker inspect \
        --format 'container={{.Name}} project={{ index .Config.Labels "com.docker.compose.project" }} workdir={{ index .Config.Labels "com.docker.compose.project.working_dir" }} ports={{json .NetworkSettings.Ports}}' \
        "$1"
}

check_project_owner() {
    local project_name="${1:-${NEOMUA_DEV_PROJECT_NAME}}"
    local container_id
    local owner
    local conflict=0

    while IFS= read -r container_id; do
        [[ -n "${container_id}" ]] || continue
        owner="$(container_owner "${container_id}")"
        if [[ "${owner}" != "${NEOMUA_DEV_ROOT}" ]]; then
            printf 'Compose project %s is already owned by another working directory:\n' "${project_name}" >&2
            container_summary "${container_id}" >&2
            conflict=1
        fi
    done < <(project_container_ids "${project_name}")

    if ((conflict)); then
        fail "choose a different DEV_SLOT; no containers were changed"
    fi
}

cleanup_test_project() {
    test_compose --profile test down -v --remove-orphans >/dev/null
}

check_port() {
    local port="$1"
    local purpose="$2"
    local container_id
    local owner
    local project
    local docker_conflict=0
    local lsof_output

    while IFS= read -r container_id; do
        [[ -n "${container_id}" ]] || continue
        owner="$(container_owner "${container_id}")"
        project="$(
            docker inspect \
                --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
                "${container_id}"
        )"
        if [[ "${project}" != "${NEOMUA_DEV_PROJECT_NAME}" || "${owner}" != "${NEOMUA_DEV_ROOT}" ]]; then
            printf 'Port %s (%s) is occupied by another container:\n' "${port}" "${purpose}" >&2
            container_summary "${container_id}" >&2
            docker_conflict=1
        fi
    done < <(docker ps -q --filter "publish=${port}")

    if ((docker_conflict)); then
        return 1
    fi

    if docker ps -q --filter "publish=${port}" | grep -q .; then
        return 0
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof_output="$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
        if [[ -n "${lsof_output}" ]]; then
            printf 'Port %s (%s) is occupied by a host process:\n%s\n' \
                "${port}" "${purpose}" "${lsof_output}" >&2
            return 1
        fi
    fi
}

preflight() {
    local group
    local failed=0

    check_project_owner
    for group in "$@"; do
        case "${group}" in
            core)
                check_port "${DEV_FRONTEND_PORT}" "frontend" || failed=1
                check_port "${DEV_BACKEND_PORT}" "backend" || failed=1
                check_port "${DEV_MODEL_GATEWAY_PORT}" "model gateway" || failed=1
                ;;
            frontend)
                check_port "${DEV_FRONTEND_PORT}" "frontend" || failed=1
                ;;
            backend)
                check_port "${DEV_BACKEND_PORT}" "backend" || failed=1
                ;;
            tools)
                check_port "${DEV_ADMINER_PORT}" "Adminer" || failed=1
                check_port "${DEV_MAILCATCHER_PORT}" "Mailcatcher UI" || failed=1
                check_port "${DEV_SMTP_PORT}" "Mailcatcher SMTP" || failed=1
                ;;
            mail)
                check_port "${DEV_MAILCATCHER_PORT}" "Mailcatcher UI" || failed=1
                check_port "${DEV_SMTP_PORT}" "Mailcatcher SMTP" || failed=1
                ;;
            proxy)
                check_port "${DEV_PROXY_HTTP_PORT}" "local proxy HTTP" || failed=1
                check_port "${DEV_TRAEFIK_DASHBOARD_PORT}" "Traefik dashboard" || failed=1
                ;;
            *)
                fail "unknown preflight group: ${group}"
                ;;
        esac
    done

    if ((failed)); then
        fail "port preflight failed; no containers were changed"
    fi

    printf 'Preflight passed: project=%s slot=%s port-base=%s\n' \
        "${NEOMUA_DEV_PROJECT_NAME}" "${NEOMUA_DEV_SLOT_NUMBER}" "${NEOMUA_DEV_PORT_BASE}"
}

print_urls() {
    printf 'Project: %s (DEV_SLOT=%s)\n' "${NEOMUA_DEV_PROJECT_NAME}" "${NEOMUA_DEV_SLOT_NUMBER}"
    printf 'Frontend: %s\n' "${DEV_FRONTEND_URL}"
    printf 'Backend docs: %s/docs\n' "${DEV_BACKEND_URL}"
    printf 'Backend health: %s/api/v1/utils/health-check/\n' "${DEV_BACKEND_URL}"
    printf 'Model gateway: %s\n' "${MODEL_GATEWAY_PUBLIC_URL}"
    printf 'Adminer (after make dev-tools-up): http://localhost:%s\n' "${DEV_ADMINER_PORT}"
    printf 'Mailcatcher (after make dev-tools-up): http://localhost:%s\n' "${DEV_MAILCATCHER_PORT}"
    printf 'Local proxy (after make dev-proxy-up): http://localhost:%s\n' "${DEV_PROXY_HTTP_PORT}"
    printf 'Traefik dashboard (after make dev-proxy-up): http://localhost:%s\n' "${DEV_TRAEFIK_DASHBOARD_PORT}"
}

usage() {
    printf '%s\n' \
        "Usage: scripts/dev-stack.sh <command>" \
        "" \
        "Commands:" \
        "  init                 Create .env from .env.example if absent" \
        "  preflight            Check core project ownership and ports" \
        "  up|down|restart      Manage the isolated core stack" \
        "  ps|logs|watch        Inspect or watch the isolated stack" \
        "  health               Check the backend health endpoint" \
        "  config [args...]     Render or validate the development Compose config" \
        "  test-config [args...] Render or validate the isolated test config" \
        "  urls                 Print deterministic addresses" \
        "  tools-up|tools-down  Manage Adminer and Mailcatcher" \
        "  proxy-up|proxy-down  Manage the optional local Traefik proxy" \
        "  frontend-hot-up      Switch this project to Vite hot reload" \
        "  frontend-hot-down    Stop this project's Vite frontend" \
        "  frontend-hot-logs    Follow this project's Vite logs" \
        "  frontend-static-up   Switch this project to the static frontend" \
        "  test-backend         Run backend tests in the running stack" \
        "  test-e2e [args...]   Run Playwright in the isolated test profile" \
        "  test-migrations      Validate Alembic upgrade and a single current head" \
        "  db-shell             Open psql inside this project's database"
}

command_name="${1:-help}"
if (($#)); then
    shift
fi

case "${command_name}" in
    init)
        if [[ -e "${NEOMUA_DEV_ROOT}/.env" ]]; then
            printf 'Existing .env preserved: %s\n' "${NEOMUA_DEV_ROOT}/.env"
        else
            cp "${NEOMUA_DEV_ROOT}/.env.example" "${NEOMUA_DEV_ROOT}/.env"
            chmod 600 "${NEOMUA_DEV_ROOT}/.env"
            printf 'Created local .env from .env.example: %s\n' "${NEOMUA_DEV_ROOT}/.env"
            printf 'The placeholder secrets are for local development only.\n'
        fi
        ;;
    preflight)
        require_env_file
        require_docker
        preflight core
        ;;
    up)
        require_env_file
        require_docker
        preflight core
        compose up -d --build
        print_urls
        ;;
    down)
        require_env_file
        require_docker
        check_project_owner
        compose down --remove-orphans
        ;;
    restart)
        require_env_file
        require_docker
        check_project_owner
        compose down --remove-orphans
        preflight core
        compose up -d --build
        print_urls
        ;;
    ps)
        require_env_file
        require_docker
        check_project_owner
        compose ps
        ;;
    logs)
        require_env_file
        require_docker
        check_project_owner
        compose logs -f "$@"
        ;;
    watch)
        require_env_file
        require_docker
        preflight core
        compose watch
        ;;
    health)
        require_env_file
        require_docker
        check_project_owner
        compose exec -T backend curl -fsS http://localhost:8000/api/v1/utils/health-check/
        printf '\n'
        ;;
    config)
        require_env_file
        require_docker
        compose config "$@"
        ;;
    test-config)
        require_env_file
        require_docker
        test_compose config "$@"
        ;;
    urls)
        print_urls
        ;;
    tools-up)
        require_env_file
        require_docker
        preflight tools
        compose --profile tools up -d adminer mailcatcher
        print_urls
        ;;
    tools-down)
        require_env_file
        require_docker
        check_project_owner
        compose --profile tools stop adminer mailcatcher
        ;;
    proxy-up)
        require_env_file
        require_docker
        preflight proxy
        compose --profile proxy up -d proxy
        print_urls
        ;;
    proxy-down)
        require_env_file
        require_docker
        check_project_owner
        compose --profile proxy stop proxy
        ;;
    frontend-hot-up)
        require_env_file
        require_docker
        preflight frontend
        compose stop frontend
        compose --profile dev-hot up -d --build frontend-dev
        print_urls
        ;;
    frontend-hot-down)
        require_env_file
        require_docker
        check_project_owner
        compose --profile dev-hot stop frontend-dev
        ;;
    frontend-hot-logs)
        require_env_file
        require_docker
        check_project_owner
        compose --profile dev-hot logs -f frontend-dev
        ;;
    frontend-static-up)
        require_env_file
        require_docker
        preflight frontend
        compose --profile dev-hot stop frontend-dev
        compose up -d frontend
        print_urls
        ;;
    test-backend)
        require_env_file
        require_docker
        check_project_owner "${NEOMUA_TEST_PROJECT_NAME}"
        cleanup_test_project
        trap cleanup_test_project EXIT
        printf 'Running backend tests in isolated project: %s\n' "${NEOMUA_TEST_PROJECT_NAME}"
        test_compose run --rm --build backend bash scripts/tests-start.sh
        trap - EXIT
        cleanup_test_project
        ;;
    test-e2e)
        require_env_file
        require_docker
        check_project_owner "${NEOMUA_TEST_PROJECT_NAME}"
        cleanup_test_project
        trap cleanup_test_project EXIT
        printf 'Running Playwright in isolated project: %s\n' "${NEOMUA_TEST_PROJECT_NAME}"
        test_compose --profile test run --rm --build playwright bunx playwright test "$@"
        trap - EXIT
        cleanup_test_project
        ;;
    test-migrations)
        require_env_file
        require_docker
        check_project_owner "${NEOMUA_TEST_PROJECT_NAME}"
        cleanup_test_project
        trap cleanup_test_project EXIT
        printf 'Validating migrations in isolated project: %s\n' "${NEOMUA_TEST_PROJECT_NAME}"
        test_compose run --rm --build backend bash -c \
            'alembic upgrade head && alembic check && test "$(alembic heads | wc -l | tr -d " ")" = "1"'
        trap - EXIT
        cleanup_test_project
        ;;
    db-shell)
        require_env_file
        require_docker
        check_project_owner
        compose exec db sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        usage >&2
        fail "unknown command: ${command_name}"
        ;;
esac

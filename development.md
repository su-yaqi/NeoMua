# NeoMua Development

Development governance, S/M/H change risk, context impact, branch rules, and the definition of done are documented in [development-process.md](./development-process.md). Graded GitHub Actions, the single required gate, and its activation checklist are documented in [development-ci.md](./development-ci.md).

## Local Docker environment

Use the repository `Makefile` for all local Compose operations. The authoritative guide is [development-workflow.md](./development-workflow.md).

Quick start:

```bash
make DEV_SLOT=0 dev-init
make DEV_SLOT=0 dev-preflight
make DEV_SLOT=0 dev-up
```

Choose a different `DEV_SLOT` for every parallel worktree. The slot determines a stable Compose project name and a deterministic port block; the preflight fails if the project is owned by another worktree or a required port is occupied.

Do not use raw `docker compose up`, `down`, or `stop` for local development. Those commands do not apply NeoMua's project ownership and conflict checks.

The default stack exposes only the frontend, backend, and model gateway. Start optional local tools or Traefik explicitly:

```bash
make DEV_SLOT=0 dev-tools-up
make DEV_SLOT=0 dev-proxy-up
```

Print the exact URLs for any slot:

```bash
make DEV_SLOT=0 dev-urls
```

PostgreSQL remains internal to the Compose project. Use Adminer or `make DEV_SLOT=0 dev-db-shell` when direct database access is needed.

## Hot reload

Switch only the selected slot between the static frontend and Docker Vite:

```bash
make DEV_SLOT=0 frontend-hot-up
make DEV_SLOT=0 frontend-hot-logs
make DEV_SLOT=0 frontend-static-up
```

Backend source is synchronized by the Compose watch configuration. Start watch mode with:

```bash
make DEV_SLOT=0 dev-watch
```

## Tests and generated code

```bash
make DEV_SLOT=0 test-backend
make DEV_SLOT=0 test-e2e
make DEV_SLOT=0 test-migrations
make test-ci-control
make generate-client
```

Backend and Playwright tests run in a derived `neomua-dev-<DEV_SLOT>-test` Compose project with disposable volumes and no published host ports. They do not reuse the running development database.

Validate the development Compose model without starting services:

```bash
make DEV_SLOT=0 dev-config
```

## The `.env` file

`make dev-init` copies `.env.example` only when `.env` does not exist. It never overwrites an existing file.

The example contains weak local-only secrets. Production and staging must inject their own values and use `compose.yml` without `compose.override.yml`; local `DEV_SLOT` ports and profiles are not part of the production topology.

## Pre-commits and code linting

NeoMua uses [prek](https://prek.j178.dev/) for repository linting and formatting. Install the hook from the backend environment:

```bash
cd backend
uv run prek install -f
```

Run all hooks manually from the repository root:

```bash
uv run prek run --all-files
```

If a hook modifies a file, review and stage the change again before committing.

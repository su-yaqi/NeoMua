# Agent 与 Harness 配置管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement v0.5 stage 1 — namespace-scoped Agent definitions, editable drafts with CAS revision control, and reusable Claude Harness Profiles with strict security validation.

**Architecture:** New `app/agent_management/` package (models, catalog, service, schemas, routes) registered into the existing FastAPI router. Three new SQLModel tables via Alembic migration. Frontend adds three TanStack Router pages + sidebar entry. No changes to v0.4 RuntimeProfile/task execution.

**Tech Stack:** Python 3.10+, FastAPI, SQLModel, Pydantic v2, Alembic, pytest; React 19, TanStack Router/Query, shadcn/ui.

## Global Constraints

- v0.5 stage 1 only — no Release/activation, no Resolver, no Skill/Tool/MCP/Plugin binding. The "能力" and "校验与发布" editor tabs are rendered as disabled placeholders.
- Do NOT modify v0.4 `RuntimeProfile`, `agent_tasks` route, runtime pages, or task execution.
- `harness_type` only `claude_code` is supported; any other value returns `unsupported_harness` error and must NOT be stored as executable.
- Environment variable denylist is absolute and takes precedence over allowlist always: `LD_PRELOAD, LD_LIBRARY_PATH, DYLD_INSERT_LIBRARIES, DYLD_LIBRARY_PATH, DYLD_FALLBACK_LIBRARY_PATH, PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, NODE_OPTIONS, NODE_PATH, PATH, SHELL, BROWSER, GIT_SSH_COMMAND, NEOMUA_*` (prefix).
- Schema layer (not just frontend) must reject: `bypassPermissions`, Shell metacharacter strings, keys named `api_key/token/password/secret`, Header values, denylisted env names.
- Cross-namespace access returns 404 uniformly.
- CAS: draft save requires `expected_revision`; conflict returns 409 + latest revision.
- Any draft field change invalidates `validated_revision` (set to null).
- All routes use `X-Namespace-Id`; Admin writes, Developer reads, User denied (403).
- Existing patterns to follow: `app/runtime/models.py` (SQLModel table style), `app/api/deps.py` (`require_namespace_admin`/`require_namespace_runtime_user`), `tests/conftest.py` (cleanup fixture), `src/api/tenantApi.ts` (axios + X-Namespace-Id interceptor), `src/routes/_layout/system.*.tsx` (file routes).

---

## File Structure

**Backend (create):**
- `backend/app/agent_management/__init__.py` — package marker.
- `backend/app/agent_management/catalog.py` — harness type catalog, env allowlist/denylist constants, config schema validation.
- `backend/app/agent_management/models.py` — `AgentDefinition`, `AgentDraft`, `HarnessProfile` SQLModel tables + enums.
- `backend/app/agent_management/service.py` — CAS draft save, draft validate, profile security validation business logic.
- `backend/app/agent_management/schemas.py` — Pydantic request/response models.
- `backend/app/agent_management/routes.py` — FastAPI router with all 8 endpoint groups.
- `backend/tests/agent_management/__init__.py` — test package marker.
- `backend/tests/agent_management/test_catalog.py` — catalog + config schema validation tests.
- `backend/tests/agent_management/test_routes.py` — API route tests (permissions, CAS, security, validation).

**Backend (modify):**
- `backend/app/api/main.py` — register `agent_management.routes.router`.
- `backend/tests/conftest.py` — add new tables to `isolate_runtime_data` cleanup.

**Migration (create):**
- `backend/app/alembic/versions/<new>_add_agent_harness_management.py`

**Frontend (create):**
- `frontend/src/routes/_layout/system.agents.tsx` — Agent list page.
- `frontend/src/routes/_layout/system.agents.$agentId.tsx` — Agent editor page.
- `frontend/src/routes/_layout/system.harnesses.tsx` — Harness config page.
- `frontend/src/components/Agents/AgentList.tsx`
- `frontend/src/components/Agents/AgentEditor.tsx`
- `frontend/src/components/Agents/CreateAgentDialog.tsx`
- `frontend/src/components/Agents/HarnessProfileSheet.tsx`

**Frontend (modify):**
- `frontend/src/api/tenantApi.ts` — add agent/harness typed API methods.
- `frontend/src/components/Sidebar/Main.tsx` or sidebar — add "Agent 管理" group with Agent + Harness entries (already supports `children`).

---

## Task 1: Harness catalog and config schema validation

**Files:**
- Create: `backend/app/agent_management/__init__.py`
- Create: `backend/app/agent_management/catalog.py`
- Test: `backend/tests/agent_management/__init__.py`
- Test: `backend/tests/agent_management/test_catalog.py`

**Interfaces:**
- Produces: `ALLOWED_HARNESS_TYPES: set[str]`, `ENV_ALLOWLIST: set[str]`, `ENV_DENYLIST: set[str]`, `ENV_RESERVED: set[str]`, `MAX_TIMEOUT_SECONDS: int`, `PermissionMode` enum, `validate_harness_type(type) -> list[Diagnostic]`, `validate_config(config, *, is_profile) -> list[Diagnostic]`, `validate_env_names(names) -> list[Diagnostic]`, `Diagnostic` pydantic model `{code, field, message}`, `HARNESS_CATALOG` dict, `environment_catalog()` function, `validate_version_constraint(expr) -> list[Diagnostic]`.

- [ ] **Step 1: Write the failing tests for catalog + config validation**

Create `backend/tests/agent_management/__init__.py` (empty) and `backend/tests/agent_management/test_catalog.py`:

```python
import pytest
from pydantic import ValidationError

from app.agent_management.catalog import (
    ENV_ALLOWLIST,
    ENV_DENYLIST,
    MAX_TIMEOUT_SECONDS,
    Diagnostic,
    environment_catalog,
    validate_config,
    validate_env_names,
    validate_harness_type,
    validate_version_constraint,
)


def test_validate_harness_type_claude_code_ok():
    diags = validate_harness_type("claude_code")
    assert diags == []


def test_validate_harness_type_unknown_rejected():
    diags = validate_harness_type("codex_cli")
    assert any(d.code == "unsupported_harness" for d in diags)


def test_validate_config_rejects_bypass_permissions():
    diags = validate_config({"permission_mode": "bypassPermissions"})
    assert any(d.code == "bypass_permissions_forbidden" for d in diags)


def test_validate_config_accepts_default_permission():
    diags = validate_config({"permission_mode": "default"})
    assert diags == []


def test_validate_config_rejects_secret_keys():
    diags = validate_config({"api_key": "xxx"})
    assert any(d.code == "secret_value_forbidden" for d in diags)


def test_validate_config_rejects_token_password_secret_keys():
    for key in ("token", "password", "secret", "Authorization"):
        diags = validate_config({key: "xxx"})
        assert any(d.code == "secret_value_forbidden" for d in diags), key


def test_validate_config_rejects_shell_metachar_strings():
    diags = validate_config({"some_flag": "ls; rm -rf /"})
    assert any(d.code == "shell_metachar_forbidden" for d in diags)


def test_validate_config_rejects_timeout_over_max():
    diags = validate_config({"timeout_seconds": MAX_TIMEOUT_SECONDS + 1})
    assert any(d.code == "timeout_exceeds_max" for d in diags)


def test_validate_config_rejects_non_positive_timeout():
    diags = validate_config({"timeout_seconds": 0})
    assert any(d.code == "timeout_invalid" for d in diags)


def test_validate_config_rejects_unknown_permission_mode():
    diags = validate_config({"permission_mode": "yolo"})
    assert any(d.code == "unknown_permission_mode" for d in diags)


def test_validate_env_names_rejects_denylist():
    diags = validate_env_names(["LD_PRELOAD"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_denylist_overrides_allowlist():
    # PATH is in denylist; even if present, must be rejected.
    diags = validate_env_names(["PATH"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_rejects_neomua_prefix():
    diags = validate_env_names(["NEOMUA_SECRET"])
    assert any(d.code == "env_denied" for d in diags)


def test_validate_env_names_rejects_non_allowlisted():
    diags = validate_env_names(["HOME"])
    assert any(d.code == "env_not_allowlisted" for d in diags)


def test_validate_env_names_accepts_allowlisted():
    diags = validate_env_names(["ANTHROPIC_MODEL"])
    assert diags == []


def test_environment_catalog_returns_three_lists():
    cat = environment_catalog()
    assert "allowlist" in cat and "reserved" in cat and "denylist" in cat
    assert "LD_PRELOAD" in cat["denylist"]
    assert "ANTHROPIC_MODEL" in cat["allowlist"]


def test_validate_version_constraint_accepts_semver_expr():
    assert validate_version_constraint(">=1.0.0") == []
    assert validate_version_constraint(">=1.0.0,<2.0.0") == []


def test_validate_version_constraint_rejects_empty():
    diags = validate_version_constraint("")
    assert any(d.code == "invalid_version_constraint" for d in diags)


def test_validate_version_constraint_rejects_garbage():
    diags = validate_version_constraint("rm -rf /")
    assert any(d.code == "invalid_version_constraint" for d in diags)


def test_diagnostic_model_fields():
    d = Diagnostic(code="x", field="f", message="m")
    assert d.code == "x" and d.field == "f" and d.message == "m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/agent_management/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent_management'`

- [ ] **Step 3: Implement catalog.py**

Create `backend/app/agent_management/__init__.py` (empty).

Create `backend/app/agent_management/catalog.py`:

```python
"""Harness type catalog, environment variable policy, and config schema validation.

All security invariants here are enforced at the schema layer (not just the
frontend): bypassPermissions, shell metacharacters, secret-named keys, and
denylisted environment variables are rejected for both HarnessProfile and
AgentDraft configs. The denylist always takes precedence over the allowlist.
"""

import re
from enum import Enum

from pydantic import BaseModel

# v0.5: only claude_code is an executable harness type. Unknown types may be
# stored as data for future migration but never pass validation as executable.
ALLOWED_HARNESS_TYPES: set[str] = {"claude_code"}

MAX_TIMEOUT_SECONDS = 3600

# Environment variables that users MAY configure (v0.5 initial set).
ENV_ALLOWLIST: set[str] = {
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "MAX_THINKING_TOKENS",
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_TIMEOUT_MS",
    "MAX_MCP_OUTPUT_TOKENS",
    "DISABLE_TELEMETRY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
}
# Allow CLAUDE_CODE_* prefix env names.
ENV_ALLOWLIST_PREFIXES: tuple[str, ...] = ("CLAUDE_CODE_",)

# Reserved names (not user-configurable but shown in catalog for clarity).
ENV_RESERVED: set[str] = set()

# Absolute denylist. Takes precedence over allowlist in all cases.
ENV_DENYLIST: set[str] = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PATH",
    "SHELL",
    "BROWSER",
    "GIT_SSH_COMMAND",
}
ENV_DENYLIST_PREFIXES: tuple[str, ...] = ("NEOMUA_", "DYLD_")

# Keys that indicate secret values and must never be stored in config JSON.
SECRET_KEY_NAMES: set[str] = {
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
}

# Shell metacharacters that indicate command injection attempts in string fields.
SHELL_METACHAR_PATTERN = re.compile(r"[;|&`$]\s*\S|&&|\|\||>\s|<\s|rm\s+-rf")

VALID_PERMISSION_MODES = {"default", "acceptEdits", "plan"}

# Version constraint: comma-separated semver comparisons, e.g. ">=1.0.0,<2.0.0".
_VERSION_CONSTRAINT_PATTERN = re.compile(
    r"^\s*(>=|<=|>|<|==|=|!=)?\s*\d+(\.\d+){0,2}"
    r"(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?"
    r"\s*(,\s*(>=|<=|>|<|==|=|!=)?\s*\d+(\.\d+){0,2}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?\s*)*$"
)


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"


class Diagnostic(BaseModel):
    code: str
    field: str
    message: str


def validate_harness_type(harness_type: str) -> list[Diagnostic]:
    if harness_type not in ALLOWED_HARNESS_TYPES:
        return [
            Diagnostic(
                code="unsupported_harness",
                field="harness_type",
                message=f"harness_type '{harness_type}' is not supported; "
                f"v0.5 only allows {sorted(ALLOWED_HARNESS_TYPES)}",
            )
        ]
    return []


def _is_denylisted(name: str) -> bool:
    if name in ENV_DENYLIST:
        return True
    return any(name.startswith(p) for p in ENV_DENYLIST_PREFIXES)


def _is_allowlisted(name: str) -> bool:
    if name in ENV_ALLOWLIST:
        return True
    return any(name.startswith(p) for p in ENV_ALLOWLIST_PREFIXES)


def validate_env_names(names: list[str]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for name in names:
        if _is_denylisted(name):
            diags.append(
                Diagnostic(
                    code="env_denied",
                    field="allowed_env_names",
                    message=f"environment variable '{name}' is denylisted and "
                    "cannot be configured; denylist takes precedence",
                )
            )
        elif not _is_allowlisted(name):
            diags.append(
                Diagnostic(
                    code="env_not_allowlisted",
                    field="allowed_env_names",
                    message=f"environment variable '{name}' is not in the platform allowlist",
                )
            )
    return diags


def _check_secret_keys(config: dict, prefix: str = "") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for key, value in config.items():
        field = f"{prefix}{key}" if prefix else key
        if key.lower() in SECRET_KEY_NAMES:
            diags.append(
                Diagnostic(
                    code="secret_value_forbidden",
                    field=field,
                    message=f"field '{key}' looks like a secret; secrets must not be "
                    "stored in config",
                )
            )
        if isinstance(value, dict):
            diags.extend(_check_secret_keys(value, field + "."))
    return diags


def _check_shell_strings(config: dict, prefix: str = "") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for key, value in config.items():
        field = f"{prefix}{key}" if prefix else key
        if isinstance(value, str) and SHELL_METACHAR_PATTERN.search(value):
            diags.append(
                Diagnostic(
                    code="shell_metachar_forbidden",
                    field=field,
                    message=f"field '{key}' contains shell metacharacters; "
                    "shell strings are not allowed",
                )
            )
        if isinstance(value, dict):
            diags.extend(_check_shell_strings(value, field + "."))
    return diags


def validate_config(config: dict, *, is_profile: bool = False) -> list[Diagnostic]:
    """Validate a non-sensitive structured config (HarnessProfile or AgentDraft).

    Rejects bypassPermissions, unknown permission modes, secret-named keys,
    shell metacharacter strings, invalid timeouts, and denylisted/non-allowlisted
    env names. Returns a list of Diagnostic (empty == valid).
    """
    diags: list[Diagnostic] = []
    diags.extend(_check_secret_keys(config))
    diags.extend(_check_shell_strings(config))

    permission_mode = config.get("permission_mode")
    if permission_mode is not None:
        if permission_mode == "bypassPermissions":
            diags.append(
                Diagnostic(
                    code="bypass_permissions_forbidden",
                    field="permission_mode",
                    message="bypassPermissions is forbidden and cannot be saved",
                )
            )
        elif permission_mode not in VALID_PERMISSION_MODES:
            diags.append(
                Diagnostic(
                    code="unknown_permission_mode",
                    field="permission_mode",
                    message=f"unknown permission_mode '{permission_mode}'",
                )
            )

    timeout = config.get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or timeout <= 0:
            diags.append(
                Diagnostic(
                    code="timeout_invalid",
                    field="timeout_seconds",
                    message="timeout_seconds must be a positive integer",
                )
            )
        elif timeout > MAX_TIMEOUT_SECONDS:
            diags.append(
                Diagnostic(
                    code="timeout_exceeds_max",
                    field="timeout_seconds",
                    message=f"timeout_seconds exceeds platform max {MAX_TIMEOUT_SECONDS}",
                )
            )

    working_dir_strategy = config.get("working_directory_strategy")
    if working_dir_strategy is not None and working_dir_strategy not in {"inherit", "require_root"}:
        diags.append(
            Diagnostic(
                code="invalid_working_directory_strategy",
                field="working_directory_strategy",
                message="working_directory_strategy must be 'inherit' or 'require_root'",
            )
        )

    env_names = config.get("allowed_env_names")
    if env_names is not None:
        if not isinstance(env_names, list):
            diags.append(
                Diagnostic(
                    code="invalid_env_names",
                    field="allowed_env_names",
                    message="allowed_env_names must be a list of strings",
                )
            )
        else:
            diags.extend(validate_env_names(env_names))

    return diags


def validate_version_constraint(expr: str) -> list[Diagnostic]:
    if not expr or not _VERSION_CONSTRAINT_PATTERN.match(expr.strip()):
        return [
            Diagnostic(
                code="invalid_version_constraint",
                field="version_constraint",
                message="version constraint must be comma-separated semver comparisons "
                "(e.g. '>=1.0.0,<2.0.0')",
            )
        ]
    return []


HARNESS_CATALOG: list[dict] = [
    {
        "type": "claude_code",
        "config_schema_version": "1.0",
        "supported": True,
        "description": "Claude Code CLI / Claude Agent SDK harness",
        "fields": [
            {"name": "permission_mode", "type": "enum", "allowed": sorted(VALID_PERMISSION_MODES)},
            {"name": "timeout_seconds", "type": "integer", "max": MAX_TIMEOUT_SECONDS},
            {"name": "working_directory_strategy", "type": "enum", "allowed": ["inherit", "require_root"]},
            {"name": "allowed_env_names", "type": "string_list", "allowlist": sorted(ENV_ALLOWLIST)},
        ],
    }
]


def environment_catalog() -> dict:
    return {
        "allowlist": sorted(ENV_ALLOWLIST),
        "reserved": sorted(ENV_RESERVED),
        "denylist": sorted(ENV_DENYLIST),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/agent_management/test_catalog.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/app/agent_management/__init__.py backend/app/agent_management/catalog.py \
  backend/tests/agent_management/__init__.py backend/tests/agent_management/test_catalog.py
git commit -m "feat(agent_management): harness catalog and config schema validation"
```

---

## Task 2: SQLModel tables for Agent/Harness

**Files:**
- Create: `backend/app/agent_management/models.py`

**Interfaces:**
- Produces: `AgentStatus` enum (`ACTIVE="active"`, `ARCHIVED="archived"`), `AgentDefinition` table, `AgentDraft` table, `HarnessProfile` table. All use `uuid` PK, `namespace_id` FK→namespace CASCADE, timestamptz via the existing `DateTime`/`SAEnum` helpers from `app.models`.

- [ ] **Step 1: Write the models**

Create `backend/app/agent_management/models.py`:

```python
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlalchemy import DateTime as _DateTime
from sqlalchemy import Enum as _SAEnum
from sqlmodel import Field, SQLModel


def DateTime(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _DateTime(*args, **kwargs))


def SAEnum(*args: Any, **kwargs: Any) -> type[Any]:
    return cast(type[Any], _SAEnum(*args, **kwargs))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentDefinition(SQLModel, table=True):
    __tablename__ = "agent_definition"
    __table_args__ = (
        UniqueConstraint("namespace_id", "slug", name="uq_agent_definition_namespace_slug"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    slug: str = Field(max_length=128)
    name: str = Field(max_length=255)
    description: str | None = None
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        sa_type=SAEnum(
            AgentStatus,
            name="agentstatus",
            values_callable=lambda v: [x.value for x in v],
        ),
    )
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class AgentDraft(SQLModel, table=True):
    __tablename__ = "agent_draft"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    agent_id: uuid.UUID = Field(
        foreign_key="agent_definition.id", nullable=False, ondelete="CASCADE", unique=True
    )
    revision: int = 1
    harness_profile_id: uuid.UUID | None = Field(
        default=None, foreign_key="harness_profile.id", ondelete="SET NULL"
    )
    provider_config_id: uuid.UUID | None = Field(
        default=None, foreign_key="llm_provider_config.id", ondelete="SET NULL"
    )
    model_id: str | None = Field(default=None, max_length=255)
    system_prompt: str = ""
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    validated_revision: int | None = Field(default=None)
    validation_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class HarnessProfile(SQLModel, table=True):
    __tablename__ = "harness_profile"
    __table_args__ = (
        UniqueConstraint("namespace_id", "name", name="uq_harness_profile_namespace_name"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    namespace_id: uuid.UUID = Field(
        foreign_key="namespace.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=255)
    harness_type: str = Field(max_length=64, default="claude_code")
    config_schema_version: str = Field(max_length=32, default="1.0")
    cli_version_constraint: str = Field(default=">=1.0.0", max_length=128)
    sdk_version_constraint: str = Field(default=">=0.2.0", max_length=128)
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    archived: bool = Field(default=False)
    created_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && python -c "from app.agent_management.models import AgentDefinition, AgentDraft, HarnessProfile, AgentStatus; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/app/agent_management/models.py
git commit -m "feat(agent_management): agent definition, draft, harness profile models"
```

---

## Task 3: Alembic migration for the three tables

**Files:**
- Create: `backend/app/alembic/versions/<rev>_add_agent_harness_management.py`

**Interfaces:**
- Consumes: alembic head `a91c4e7b2d10` (current latest).
- Produces: tables `agent_definition`, `agent_draft`, `harness_profile` + `agentstatus` enum.

- [ ] **Step 1: Determine current alembic head and create migration**

Run: `cd backend && python -m alembic heads`
Expected: shows `a91c4e7b2d10 (head)`.

Create the migration file `backend/app/alembic/versions/b2c5e8f9a301_add_agent_harness_management.py`. Use a new revision id `b2c5e8f9a301`, `down_revision = "a91c4e7b2d10"`:

```python
"""add agent harness management

Revision ID: b2c5e8f9a301
Revises: a91c4e7b2d10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c5e8f9a301"
down_revision: str | None = "a91c4e7b2d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    agent_status = postgresql.ENUM(
        "active", "archived", name="agentstatus", create_type=False
    )
    bind = op.get_bind()
    agent_status.create(bind, checkfirst=True)

    op.create_table(
        "agent_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", agent_status, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["namespace.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "namespace_id", "slug", name="uq_agent_definition_namespace_slug"
        ),
    )
    op.create_index(
        "ix_agent_definition_namespace_id", "agent_definition", ["namespace_id"]
    )

    op.create_table(
        "harness_profile",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("harness_type", sa.String(length=64), nullable=False),
        sa.Column("config_schema_version", sa.String(length=32), nullable=False),
        sa.Column("cli_version_constraint", sa.String(length=128), nullable=False),
        sa.Column("sdk_version_constraint", sa.String(length=128), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["namespace.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "namespace_id", "name", name="uq_harness_profile_namespace_name"
        ),
    )
    op.create_index(
        "ix_harness_profile_namespace_id", "harness_profile", ["namespace_id"]
    )

    op.create_table(
        "agent_draft",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("harness_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validated_revision", sa.Integer(), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agent_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["harness_profile_id"], ["harness_profile.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("agent_id", name="uq_agent_draft_agent"),
    )


def downgrade() -> None:
    op.drop_table("agent_draft")
    op.drop_table("harness_profile")
    op.drop_index("ix_harness_profile_namespace_id", table_name="harness_profile")
    op.drop_table("agent_definition")
    op.drop_index("ix_agent_definition_namespace_id", table_name="agent_definition")
    agent_status = postgresql.ENUM(
        "active", "archived", name="agentstatus", create_type=False
    )
    agent_status.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 2: Run the migration**

Run: `cd backend && python -m alembic upgrade head`
Expected: `Running upgrade a91c4e7b2d10 -> b2c5e8f9a301, add agent harness management`.

- [ ] **Step 3: Verify tables exist**

Run: `cd backend && python -c "
from sqlalchemy import inspect
from app.core.db import engine
i = inspect(engine)
for t in ('agent_definition','agent_draft','harness_profile'):
    assert t in i.get_table_names(), t
print('tables ok')
"`
Expected: prints `tables ok`.

- [ ] **Step 4: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/app/alembic/versions/b2c5e8f9a301_add_agent_harness_management.py
git commit -m "feat(agent_management): alembic migration for agent/harness tables"
```

---

## Task 4: Service layer — CAS draft save and validation

**Files:**
- Create: `backend/app/agent_management/service.py`

**Interfaces:**
- Consumes: `AgentDefinition`, `AgentDraft`, `HarnessProfile` from models; `validate_config`, `validate_harness_type`, `validate_version_constraint`, `Diagnostic`, `environment_catalog` from catalog; `crud.get_namespace_role`, `LlmProviderConfig`, `LlmProviderModel`, `RuntimeProfile`, `RuntimeNode` for validation lookups.
- Produces: `save_draft(session, agent, expected_revision, fields) -> AgentDraft` (raises `DraftConflict`), `validate_draft(session, agent, namespace_id) -> ValidationResult`, `ValidationResult` pydantic model, `DraftConflict` exception, `create_agent(session, namespace_id, slug, name, description, user_id) -> AgentDefinition`, `archive_agent(session, agent) -> AgentDefinition`, `is_profile_referenced(session, profile_id) -> bool`, `ValidationStatus` enum.

- [ ] **Step 1: Write the service module**

Create `backend/app/agent_management/service.py`:

```python
"""Business logic for Agent drafts and Harness Profiles.

CAS revision control, draft validation (model/harness/version/security),
and profile reference checks. All security validation delegates to catalog.py
so the schema-layer invariants are enforced for every write path.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.agent_management.catalog import (
    Diagnostic,
    environment_catalog,
    validate_config,
    validate_harness_type,
    validate_version_constraint,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    AgentStatus,
    HarnessProfile,
)
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.models import RuntimeNode, RuntimeProfile, RuntimeType


class DraftConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"draft revision conflict; current revision is {current_revision}")


class ValidationStatus(str):
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    STALE = "stale"
    ERROR = "error"


class TargetCompatibility(BaseModel):
    runtime_profile_id: uuid.UUID
    runtime_type: str
    cli_version: str | None = None
    sdk_version: str | None = None
    compatible: bool | None = None
    reason: str | None = None


class ValidationResult(BaseModel):
    validated_revision: int
    status: str
    errors: list[Diagnostic]
    warnings: list[Diagnostic]
    target_compatibility: list[TargetCompatibility]


# Patterns that hint at secret leakage in system prompts (advisory only).
_SECRET_HINT_PATTERNS = (
    "sk-ant-",
    "AKIA",
    "-----BEGIN",
)


def create_agent(
    session: Session,
    namespace_id: uuid.UUID,
    *,
    slug: str,
    name: str,
    description: str | None,
    user_id: uuid.UUID,
) -> AgentDefinition:
    agent = AgentDefinition(
        namespace_id=namespace_id,
        slug=slug,
        name=name,
        description=description,
        status=AgentStatus.ACTIVE,
        created_by=user_id,
    )
    draft = AgentDraft(agent_id=agent.id, revision=1)
    session.add(agent)
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise _slug_conflict_or_reraise(exc) from exc
    return agent


def _slug_conflict_or_reraise(exc: IntegrityError) -> Exception:
    msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
    if "uq_agent_definition_namespace_slug" in msg or "slug" in msg:
        return ValueError("slug already exists in this namespace")
    return exc


def save_draft(
    session: Session,
    agent: AgentDefinition,
    *,
    expected_revision: int,
    harness_profile_id: uuid.UUID | None = None,
    provider_config_id: uuid.UUID | None = None,
    model_id: str | None = None,
    system_prompt: str | None = None,
    config: dict | None = None,
) -> AgentDraft:
    """CAS-save a draft. Locks the row; bumps revision on success.

    Any field change invalidates validated_revision (set to None) because the
    old validation no longer applies to the new content.
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    if draft is None:
        raise ValueError("draft not found")
    if draft.revision != expected_revision:
        raise DraftConflict(draft.revision)

    changed = False
    if harness_profile_id is not None and harness_profile_id != draft.harness_profile_id:
        draft.harness_profile_id = harness_profile_id
        changed = True
    if provider_config_id is not None and provider_config_id != draft.provider_config_id:
        draft.provider_config_id = provider_config_id
        changed = True
    if model_id is not None and model_id != draft.model_id:
        draft.model_id = model_id
        changed = True
    if system_prompt is not None and system_prompt != draft.system_prompt:
        draft.system_prompt = system_prompt
        changed = True
    if config is not None and config != draft.config:
        draft.config = config
        changed = True

    if changed:
        draft.revision += 1
        # Invalidate any prior validation — it was for older content.
        draft.validated_revision = None
        draft.validation_result = None
        draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.flush()
    return draft


def archive_agent(session: Session, agent: AgentDefinition) -> AgentDefinition:
    agent.status = AgentStatus.ARCHIVED
    agent.updated_at = datetime.now(timezone.utc)
    session.add(agent)
    session.flush()
    return agent


def is_profile_referenced(session: Session, profile_id: uuid.UUID) -> bool:
    """True if any non-archived agent draft references this profile."""
    stmt = (
        select(AgentDraft)
        .join(AgentDefinition, AgentDraft.agent_id == AgentDefinition.id)
        .where(
            AgentDraft.harness_profile_id == profile_id,
            AgentDefinition.status == AgentStatus.ACTIVE,
        )
    )
    return session.exec(stmt).first() is not None


def _validate_model(
    session: Session, namespace_id: uuid.UUID, draft: AgentDraft
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if draft.provider_config_id is None or not draft.model_id:
        diags.append(
            Diagnostic(
                code="model_not_selected",
                field="model_id",
                message="model and provider config are required",
            )
        )
        return diags
    config = session.get(LlmProviderConfig, draft.provider_config_id)
    if config is None or config.namespace_id != namespace_id:
        diags.append(
            Diagnostic(
                code="cross_namespace_model",
                field="provider_config_id",
                message="provider config does not belong to this namespace",
            )
        )
        return diags
    if not config.enabled:
        diags.append(
            Diagnostic(
                code="model_stale",
                field="provider_config_id",
                message="provider config is disabled",
            )
        )
    model = session.exec(
        select(LlmProviderModel).where(
            LlmProviderModel.provider_config_id == config.id,
            LlmProviderModel.model_id == draft.model_id,
        )
    ).first()
    if model is None:
        diags.append(
            Diagnostic(
                code="model_not_found",
                field="model_id",
                message=f"model '{draft.model_id}' not found in this provider config",
            )
        )
    elif not model.is_enabled:
        diags.append(
            Diagnostic(
                code="model_stale",
                field="model_id",
                message=f"model '{draft.model_id}' is disabled",
            )
        )
    return diags


def _validate_harness(
    session: Session, namespace_id: uuid.UUID, draft: AgentDraft
) -> tuple[list[Diagnostic], HarnessProfile | None]:
    diags: list[Diagnostic] = []
    if draft.harness_profile_id is None:
        diags.append(
            Diagnostic(
                code="harness_not_selected",
                field="harness_profile_id",
                message="harness profile is required",
            )
        )
        return diags, None
    profile = session.get(HarnessProfile, draft.harness_profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        diags.append(
            Diagnostic(
                code="cross_namespace_harness",
                field="harness_profile_id",
                message="harness profile does not belong to this namespace",
            )
        )
        return diags, None
    if profile.archived:
        diags.append(
            Diagnostic(
                code="harness_archived",
                field="harness_profile_id",
                message="harness profile is archived",
            )
        )
    diags.extend(validate_harness_type(profile.harness_type))
    return diags, profile


def _build_target_compatibility(
    session: Session, namespace_id: uuid.UUID, profile: HarnessProfile | None
) -> list[TargetCompatibility]:
    if profile is None:
        return []
    runtimes = session.exec(
        select(RuntimeProfile).where(RuntimeProfile.namespace_id == namespace_id)
    ).all()
    results: list[TargetCompatibility] = []
    for rt in runtimes:
        cli_version: str | None = None
        sdk_version: str | None = None
        if rt.runtime_type == RuntimeType.NODE:
            node = session.exec(
                select(RuntimeNode).where(
                    RuntimeNode.runtime_profile_id == rt.id,
                    RuntimeNode.revoked_at.is_(None),  # type: ignore[attr-defined]
                )
            ).first()
            if node:
                cli_version = node.agent_version
                sdk_version = node.sdk_version
        else:
            # Platform runtime: use runtime-worker reported versions from config.
            cli_version = rt.config.get("reported_cli_version")
            sdk_version = rt.config.get("reported_sdk_version")

        compatible: bool | None
        reason: str | None
        if cli_version is None and sdk_version is None:
            compatible = None
            reason = "unknown"
        else:
            compatible = True
            reason = None
        results.append(
            TargetCompatibility(
                runtime_profile_id=rt.id,
                runtime_type=rt.runtime_type.value,
                cli_version=cli_version,
                sdk_version=sdk_version,
                compatible=compatible,
                reason=reason,
            )
        )
    return results


def validate_draft(
    session: Session, agent: AgentDefinition, namespace_id: uuid.UUID
) -> ValidationResult:
    """Run full draft validation. Persists validated_revision + result on success.

    Returns the ValidationResult. Errors block a future publish; warnings do not.
    Target compatibility 'unknown' is treated as an error (blocks publish).
    """
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    assert draft is not None  # created with agent

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    errors.extend(_validate_model(session, namespace_id, draft))
    harness_errors, profile = _validate_harness(session, namespace_id, draft)
    errors.extend(harness_errors)

    # Config security validation (schema-layer invariants).
    errors.extend(validate_config(draft.config))

    if profile is not None:
        errors.extend(validate_config(profile.config, is_profile=True))
        vc_errors = validate_version_constraint(profile.cli_version_constraint)
        errors.extend(
            Diagnostic(
                code=d.code,
                field=f"cli_version_constraint.{d.field}",
                message=d.message,
            )
            for d in vc_errors
        )
        vc_errors = validate_version_constraint(profile.sdk_version_constraint)
        errors.extend(
            Diagnostic(
                code=d.code,
                field=f"sdk_version_constraint.{d.field}",
                message=d.message,
            )
            for d in vc_errors
        )

    # system_prompt checks
    if not draft.system_prompt.strip():
        errors.append(
            Diagnostic(
                code="system_prompt_empty",
                field="system_prompt",
                message="system prompt is required",
            )
        )
    for hint in _SECRET_HINT_PATTERNS:
        if hint in draft.system_prompt:
            warnings.append(
                Diagnostic(
                    code="potential_secret_in_prompt",
                    field="system_prompt",
                    message="system prompt may contain a secret; please verify",
                )
            )
            break

    target_compat = _build_target_compatibility(session, namespace_id, profile)
    # 'unknown' targets block publish.
    for tc in target_compat:
        if tc.compatible is None:
            errors.append(
                Diagnostic(
                    code="target_version_unknown",
                    field="target_compatibility",
                    message=f"runtime {tc.runtime_profile_id} has not reported "
                    "CLI/SDK versions; compatibility is unknown",
                )
            )

    status = "validated" if not errors else "error"

    draft.validated_revision = draft.revision if not errors else None
    draft.validation_result = {
        "status": status,
        "errors": [e.model_dump() for e in errors],
        "warnings": [w.model_dump() for w in warnings],
    }
    draft.updated_at = datetime.now(timezone.utc)
    session.add(draft)
    session.flush()

    return ValidationResult(
        validated_revision=draft.revision if not errors else draft.revision,
        status=status,
        errors=errors,
        warnings=warnings,
        target_compatibility=target_compat,
    )


def draft_validation_status(draft: AgentDraft) -> str:
    """Derive a display status from a draft's persisted validation state."""
    if draft.validation_result is None:
        return ValidationStatus.UNVALIDATED
    if draft.validated_revision is None:
        return ValidationStatus.STALE if draft.validation_result.get("status") == "validated" else ValidationStatus.ERROR
    if draft.validated_revision != draft.revision:
        return ValidationStatus.STALE
    return draft.validation_result.get("status", ValidationStatus.UNVALIDATED)
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && python -c "from app.agent_management.service import create_agent, save_draft, validate_draft, DraftConflict; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/app/agent_management/service.py
git commit -m "feat(agent_management): CAS draft save and validation service"
```

---

## Task 5: Schemas and API routes

**Files:**
- Create: `backend/app/agent_management/schemas.py`
- Create: `backend/app/agent_management/routes.py`
- Modify: `backend/app/api/main.py`

**Interfaces:**
- Consumes: service functions from Task 4; models from Task 2; deps `require_namespace_admin`, `require_namespace_runtime_user`, `CurrentUser`, `SessionDep` from `app.api.deps`; `crud.get_namespace_role`, `NamespaceRole`.
- Produces: `router` APIRouter with prefix tags; all endpoints listed in the spec §5.

- [ ] **Step 1: Write schemas.py**

Create `backend/app/agent_management/schemas.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent_management.catalog import Diagnostic
from app.agent_management.service import TargetCompatibility, ValidationResult


class AgentCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None  # "active" | "archived"


class DraftSave(BaseModel):
    expected_revision: int
    harness_profile_id: uuid.UUID | None = None
    provider_config_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=255)
    system_prompt: str | None = None
    config: dict | None = None


class AgentDraftPublic(BaseModel):
    agent_id: uuid.UUID
    revision: int
    harness_profile_id: uuid.UUID | None
    provider_config_id: uuid.UUID | None
    model_id: str | None
    system_prompt: str
    config: dict
    validated_revision: int | None
    validation_result: dict | None
    validation_status: str
    updated_at: datetime


class AgentPublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class AgentListItem(AgentPublic):
    draft_revision: int
    validation_status: str
    harness_type: str | None = None
    model_id: str | None = None


class AgentsPublic(BaseModel):
    data: list[AgentListItem]
    count: int


class HarnessProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    harness_type: str = Field(default="claude_code", max_length=64)
    config_schema_version: str = Field(default="1.0", max_length=32)
    cli_version_constraint: str = Field(default=">=1.0.0", max_length=128)
    sdk_version_constraint: str = Field(default=">=0.2.0", max_length=128)
    config: dict = Field(default_factory=dict)


class HarnessProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cli_version_constraint: str | None = Field(default=None, max_length=128)
    sdk_version_constraint: str | None = Field(default=None, max_length=128)
    config: dict | None = None
    archived: bool | None = None


class HarnessProfilePublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    name: str
    harness_type: str
    config_schema_version: str
    cli_version_constraint: str
    sdk_version_constraint: str
    config: dict
    archived: bool
    referenced_by_agents: bool
    created_at: datetime
    updated_at: datetime


class HarnessProfilesPublic(BaseModel):
    data: list[HarnessProfilePublic]
    count: int


class HarnessCatalogPublic(BaseModel):
    harnesses: list[dict]


class EnvironmentCatalogPublic(BaseModel):
    allowlist: list[str]
    reserved: list[str]
    denylist: list[str]
```

- [ ] **Step 2: Write routes.py**

Create `backend/app/agent_management/routes.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select

from app import crud
from app.agent_management.catalog import (
    environment_catalog,
    validate_config,
    validate_harness_type,
    validate_version_constraint,
    HARNESS_CATALOG,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    AgentStatus,
    HarnessProfile,
)
from app.agent_management.schemas import (
    AgentCreate,
    AgentDraftPublic,
    AgentListItem,
    AgentPublic,
    AgentsPublic,
    AgentUpdate,
    DraftSave,
    EnvironmentCatalogPublic,
    HarnessCatalogPublic,
    HarnessProfileCreate,
    HarnessProfilePublic,
    HarnessProfilesPublic,
    HarnessProfileUpdate,
)
from app.agent_management.service import (
    DraftConflict,
    create_agent,
    draft_validation_status,
    is_profile_referenced,
    save_draft,
    validate_draft,
)
from app.api.deps import CurrentUser, SessionDep
from app.models import NamespaceRole
from app.runtime.models import RuntimeProfile

router = APIRouter()


def _require_admin(
    session: SessionDep, current_user: CurrentUser, namespace_id: uuid.UUID
) -> None:
    if current_user.is_superuser:
        return
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=namespace_id
    )
    if role != NamespaceRole.ADMIN:
        raise HTTPException(403, "Namespace admin privilege required")


def _get_agent(
    session: SessionDep, agent_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentDefinition:
    agent = session.get(AgentDefinition, agent_id)
    if agent is None or agent.namespace_id != namespace_id:
        raise HTTPException(404, "Agent not found")
    return agent


def _get_draft(session: SessionDep, agent: AgentDefinition) -> AgentDraft:
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is None:
        raise HTTPException(404, "Draft not found")
    return draft


def _draft_public(draft: AgentDraft) -> AgentDraftPublic:
    return AgentDraftPublic(
        agent_id=draft.agent_id,
        revision=draft.revision,
        harness_profile_id=draft.harness_profile_id,
        provider_config_id=draft.provider_config_id,
        model_id=draft.model_id,
        system_prompt=draft.system_prompt,
        config=draft.config,
        validated_revision=draft.validated_revision,
        validation_result=draft.validation_result,
        validation_status=draft_validation_status(draft),
        updated_at=draft.updated_at,
    )


def _agent_public(agent: AgentDefinition) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        namespace_id=agent.namespace_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status.value,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _profile_public(
    session: SessionDep, profile: HarnessProfile
) -> HarnessProfilePublic:
    return HarnessProfilePublic(
        id=profile.id,
        namespace_id=profile.namespace_id,
        name=profile.name,
        harness_type=profile.harness_type,
        config_schema_version=profile.config_schema_version,
        cli_version_constraint=profile.cli_version_constraint,
        sdk_version_constraint=profile.sdk_version_constraint,
        config=profile.config,
        archived=profile.archived,
        referenced_by_agents=is_profile_referenced(session, profile.id),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# --- Agents ---


@router.get("/agents", response_model=AgentsPublic)
def list_agents(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(  # type: ignore[assignment]
        # runtime_user allows admin+developer read
    ),
) -> AgentsPublic:
    from app.api.deps import require_namespace_runtime_user

    pass


@router.post("/agents", response_model=AgentPublic, status_code=201)
def create_agent_endpoint(
    body: AgentCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),  # type: ignore[name-defined]
) -> AgentPublic:
    _require_admin(session, current_user, namespace_id)
    try:
        agent = create_agent(
            session,
            namespace_id,
            slug=body.slug,
            name=body.name,
            description=body.description,
            user_id=current_user.id,
        )
        session.commit()
        session.refresh(agent)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _agent_public(agent)
```

NOTE: The above `list_agents` has a dependency injection bug (placeholder). Rewrite the full routes file cleanly — do not leave the broken `list_agents`. The correct file (write this, replacing the placeholder version entirely) is:

Create `backend/app/agent_management/routes.py` (final version):

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, select

from app import crud
from app.agent_management.catalog import (
    environment_catalog,
    validate_config,
    validate_harness_type,
    validate_version_constraint,
    HARNESS_CATALOG,
)
from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    AgentStatus,
    HarnessProfile,
)
from app.agent_management.schemas import (
    AgentCreate,
    AgentDraftPublic,
    AgentListItem,
    AgentPublic,
    AgentsPublic,
    AgentUpdate,
    DraftSave,
    EnvironmentCatalogPublic,
    HarnessCatalogPublic,
    HarnessProfileCreate,
    HarnessProfilePublic,
    HarnessProfilesPublic,
    HarnessProfileUpdate,
)
from app.agent_management.service import (
    DraftConflict,
    create_agent,
    draft_validation_status,
    is_profile_referenced,
    save_draft,
    validate_draft,
)
from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.models import NamespaceRole

router = APIRouter()


def _require_admin(
    session: SessionDep, current_user: CurrentUser, namespace_id: uuid.UUID
) -> None:
    if current_user.is_superuser:
        return
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=namespace_id
    )
    if role != NamespaceRole.ADMIN:
        raise HTTPException(403, "Namespace admin privilege required")


def _get_agent(
    session: SessionDep, agent_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentDefinition:
    agent = session.get(AgentDefinition, agent_id)
    if agent is None or agent.namespace_id != namespace_id:
        raise HTTPException(404, "Agent not found")
    return agent


def _get_draft(session: SessionDep, agent: AgentDefinition) -> AgentDraft:
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is None:
        raise HTTPException(404, "Draft not found")
    return draft


def _draft_public(draft: AgentDraft) -> AgentDraftPublic:
    return AgentDraftPublic(
        agent_id=draft.agent_id,
        revision=draft.revision,
        harness_profile_id=draft.harness_profile_id,
        provider_config_id=draft.provider_config_id,
        model_id=draft.model_id,
        system_prompt=draft.system_prompt,
        config=draft.config,
        validated_revision=draft.validated_revision,
        validation_result=draft.validation_result,
        validation_status=draft_validation_status(draft),
        updated_at=draft.updated_at,
    )


def _agent_public(agent: AgentDefinition) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        namespace_id=agent.namespace_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status.value,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _agent_list_item(
    session: SessionDep, agent: AgentDefinition, draft: AgentDraft
) -> AgentListItem:
    harness_type: str | None = None
    if draft.harness_profile_id:
        profile = session.get(HarnessProfile, draft.harness_profile_id)
        if profile and profile.namespace_id == agent.namespace_id:
            harness_type = profile.harness_type
    return AgentListItem(
        id=agent.id,
        namespace_id=agent.namespace_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status.value,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        draft_revision=draft.revision,
        validation_status=draft_validation_status(draft),
        harness_type=harness_type,
        model_id=draft.model_id,
    )


def _profile_public(
    session: SessionDep, profile: HarnessProfile
) -> HarnessProfilePublic:
    return HarnessProfilePublic(
        id=profile.id,
        namespace_id=profile.namespace_id,
        name=profile.name,
        harness_type=profile.harness_type,
        config_schema_version=profile.config_schema_version,
        cli_version_constraint=profile.cli_version_constraint,
        sdk_version_constraint=profile.sdk_version_constraint,
        config=profile.config,
        archived=profile.archived,
        referenced_by_agents=is_profile_referenced(session, profile.id),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# --- Agents ---


@router.get("/agents", response_model=AgentsPublic)
def list_agents(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentsPublic:
    agents = session.exec(
        select(AgentDefinition)
        .where(AgentDefinition.namespace_id == namespace_id)
        .order_by(col(AgentDefinition.created_at).desc())
    ).all()
    items: list[AgentListItem] = []
    for agent in agents:
        draft = session.exec(
            select(AgentDraft).where(AgentDraft.agent_id == agent.id)
        ).first()
        if draft is None:
            continue
        items.append(_agent_list_item(session, agent, draft))
    return AgentsPublic(data=items, count=len(items))


@router.post("/agents", response_model=AgentPublic, status_code=201)
def create_agent_endpoint(
    body: AgentCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentPublic:
    try:
        agent = create_agent(
            session,
            namespace_id,
            slug=body.slug,
            name=body.name,
            description=body.description,
            user_id=current_user.id,
        )
        session.commit()
        session.refresh(agent)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _agent_public(agent)


@router.get("/agents/{agent_id}", response_model=AgentPublic)
def read_agent(
    agent_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentPublic:
    return _agent_public(_get_agent(session, agent_id, namespace_id))


@router.patch("/agents/{agent_id}", response_model=AgentPublic)
def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be modified")
    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.status is not None:
        if body.status not in {AgentStatus.ACTIVE.value, AgentStatus.ARCHIVED.value}:
            raise HTTPException(422, "status must be 'active' or 'archived'")
        agent.status = AgentStatus(body.status)
    from datetime import datetime, timezone

    agent.updated_at = datetime.now(timezone.utc)
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _agent_public(agent)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(
    agent_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    # v0.5 stage 1: no releases yet, but enforce the rule preemptively.
    # (When releases exist in stage 6, check release reference here -> 409.)
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id)
    ).first()
    if draft is not None:
        session.delete(draft)
    session.delete(agent)
    session.commit()


@router.get("/agents/{agent_id}/draft", response_model=AgentDraftPublic)
def read_draft(
    agent_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> AgentDraftPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    return _draft_public(_get_draft(session, agent))


@router.put("/agents/{agent_id}/draft", response_model=AgentDraftPublic)
def save_draft_endpoint(
    agent_id: uuid.UUID,
    body: DraftSave,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> AgentDraftPublic:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be edited")
    # Pre-validate config security before saving.
    if body.config is not None:
        diags = validate_config(body.config)
        if diags:
            raise HTTPException(422, {"errors": [d.model_dump() for d in diags]})
    try:
        draft = save_draft(
            session,
            agent,
            expected_revision=body.expected_revision,
            harness_profile_id=body.harness_profile_id,
            provider_config_id=body.provider_config_id,
            model_id=body.model_id,
            system_prompt=body.system_prompt,
            config=body.config,
        )
        session.commit()
        session.refresh(draft)
    except DraftConflict as exc:
        session.rollback()
        raise HTTPException(
            409,
            {
                "code": "draft_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
    return _draft_public(draft)


@router.post("/agents/{agent_id}/draft/validate")
def validate_draft_endpoint(
    agent_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict:
    agent = _get_agent(session, agent_id, namespace_id)
    _require_admin(session, current_user, namespace_id)
    if agent.status == AgentStatus.ARCHIVED:
        raise HTTPException(409, "Archived agent cannot be validated")
    result = validate_draft(session, agent, namespace_id)
    session.commit()
    return result.model_dump(mode="json")


# --- Harness Profiles ---


@router.get("/harness-profiles", response_model=HarnessProfilesPublic)
def list_profiles(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessProfilesPublic:
    profiles = session.exec(
        select(HarnessProfile)
        .where(HarnessProfile.namespace_id == namespace_id)
        .order_by(col(HarnessProfile.created_at).desc())
    ).all()
    data = [_profile_public(session, p) for p in profiles]
    return HarnessProfilesPublic(data=data, count=len(data))


@router.post("/harness-profiles", response_model=HarnessProfilePublic, status_code=201)
def create_profile(
    body: HarnessProfileCreate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> HarnessProfilePublic:
    _require_admin(session, current_user, namespace_id)
    # Schema-layer security validation before persisting.
    errors: list = []
    errors.extend(validate_harness_type(body.harness_type))
    errors.extend(validate_config(body.config, is_profile=True))
    errors.extend(validate_version_constraint(body.cli_version_constraint))
    errors.extend(validate_version_constraint(body.sdk_version_constraint))
    if errors:
        raise HTTPException(422, {"errors": [e.model_dump() for e in errors]})
    profile = HarnessProfile(
        namespace_id=namespace_id,
        name=body.name,
        harness_type=body.harness_type,
        config_schema_version=body.config_schema_version,
        cli_version_constraint=body.cli_version_constraint,
        sdk_version_constraint=body.sdk_version_constraint,
        config=body.config,
        archived=False,
        created_by=current_user.id,
    )
    session.add(profile)
    try:
        session.commit()
        session.refresh(profile)
    except Exception as exc:  # unique constraint etc.
        session.rollback()
        msg = str(exc).lower()
        if "uq_harness_profile_namespace_name" in msg or "name" in msg:
            raise HTTPException(409, "Profile name already exists in this namespace") from exc
        raise
    return _profile_public(session, profile)


@router.get("/harness-profiles/{profile_id}", response_model=HarnessProfilePublic)
def read_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessProfilePublic:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    return _profile_public(session, profile)


@router.patch("/harness-profiles/{profile_id}", response_model=HarnessProfilePublic)
def update_profile(
    profile_id: uuid.UUID,
    body: HarnessProfileUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> HarnessProfilePublic:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    _require_admin(session, current_user, namespace_id)
    # Validate new config/values before saving.
    candidate_config = body.config if body.config is not None else profile.config
    errors: list = []
    errors.extend(validate_config(candidate_config, is_profile=True))
    if body.cli_version_constraint is not None:
        errors.extend(validate_version_constraint(body.cli_version_constraint))
    if body.sdk_version_constraint is not None:
        errors.extend(validate_version_constraint(body.sdk_version_constraint))
    if errors:
        raise HTTPException(422, {"errors": [e.model_dump() for e in errors]})
    if body.name is not None:
        profile.name = body.name
    if body.cli_version_constraint is not None:
        profile.cli_version_constraint = body.cli_version_constraint
    if body.sdk_version_constraint is not None:
        profile.sdk_version_constraint = body.sdk_version_constraint
    if body.config is not None:
        profile.config = body.config
    if body.archived is not None:
        profile.archived = body.archived
    from datetime import datetime, timezone

    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)
    try:
        session.commit()
        session.refresh(profile)
    except Exception as exc:
        session.rollback()
        raise HTTPException(409, "Profile name already exists in this namespace") from exc
    return _profile_public(session, profile)


@router.delete("/harness-profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> None:
    profile = session.get(HarnessProfile, profile_id)
    if profile is None or profile.namespace_id != namespace_id:
        raise HTTPException(404, "Harness profile not found")
    _require_admin(session, current_user, namespace_id)
    if is_profile_referenced(session, profile.id):
        raise HTTPException(409, "Profile is referenced by agents; archive it instead")
    session.delete(profile)
    session.commit()


# --- Catalogs ---


@router.get("/harnesses/catalog", response_model=HarnessCatalogPublic)
def harness_catalog(
    _: CurrentUser,
    __: uuid.UUID = Depends(require_namespace_runtime_user),
) -> HarnessCatalogPublic:
    return HarnessCatalogPublic(harnesses=HARNESS_CATALOG)


@router.get("/harnesses/environment-catalog", response_model=EnvironmentCatalogPublic)
def env_catalog(
    _: CurrentUser,
    __: uuid.UUID = Depends(require_namespace_runtime_user),
) -> EnvironmentCatalogPublic:
    cat = environment_catalog()
    return EnvironmentCatalogPublic(**cat)
```

- [ ] **Step 3: Register router in api/main.py**

Modify `backend/app/api/main.py`. Add import alongside the other route imports:

```python
from app.api.routes import (
    agent_tasks,
    items,
    llm_provider_configs,
    login,
    namespaces,
    node_enrollment,
    node_socket,
    private,
    runtime_artifacts,
    runtime_internal,
    runtimes,
    users,
    utils,
)
from app.agent_management.routes import router as agent_management_router
```

And add before the private router block:

```python
api_router.include_router(agent_management_router)
```

- [ ] **Step 4: Verify the app imports and routes are registered**

Run: `cd backend && python -c "
from app.main import app
paths = [r.path for r in app.routes]
assert '/api/v1/agents' in paths, paths
assert '/api/v1/harness-profiles' in paths, paths
assert '/api/v1/harnesses/catalog' in paths, paths
print('routes ok')
"`
Expected: prints `routes ok`.

- [ ] **Step 5: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/app/agent_management/schemas.py backend/app/agent_management/routes.py backend/app/api/main.py
git commit -m "feat(agent_management): agent and harness profile API routes"
```

---

## Task 6: Update conftest cleanup + write route tests

**Files:**
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/agent_management/test_routes.py`

**Interfaces:**
- Consumes: existing test fixtures `client`, `superuser_token_headers`, `normal_user_token_headers`, `db`; helper patterns from `tests/api/routes/test_llm_provider_configs.py`.

- [ ] **Step 1: Inspect an existing route test for the namespace header pattern**

Run: `cd backend && sed -n '1,70p' tests/api/routes/test_llm_provider_configs.py`
Study how it sets `X-Namespace-Id` headers and creates namespaces. Reuse the same helpers.

- [ ] **Step 2: Update conftest.py and tests/utils/db.py cleanup**

Two cleanup functions must delete the new tables:

**a) `backend/tests/conftest.py`** — `isolate_runtime_data` (per-test autouse). Add imports at top:

```python
from app.agent_management.models import (
    AgentDraft,
    AgentDefinition,
    HarnessProfile,
)
```

In the `clean()` function, at the START (before existing deletes), add:

```python
        db.execute(delete(AgentDraft))
        db.execute(delete(AgentDefinition))
        db.execute(delete(HarnessProfile))
```

**b) `backend/tests/utils/db.py`** — `cleanup_test_data` (session-scope teardown). Add the same imports and the same three `delete(...)` calls at the START of the function (before the existing `delete(AgentEvent)` etc.), since agent_draft references harness_profile and llm_provider_config via SET NULL, and agent_definition references namespace via CASCADE — deleting them first is safe.

- [ ] **Step 3: Write route tests**

Create `backend/tests/agent_management/test_routes.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def _ns_headers(headers: dict[str, str], namespace_id: str) -> dict[str, str]:
    h = dict(headers)
    h["X-Namespace-Id"] = namespace_id
    return h


@pytest.fixture
def namespace_setup(client: TestClient, superuser_token_headers: dict[str, str]):
    # Create a namespace via the platform endpoint (POST /platform/namespaces).
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={
            "name": f"ns-agents-{uuid.uuid4().hex[:6]}",
            "code": f"ns-agents-{uuid.uuid4().hex[:6]}",
            "is_active": True,
        },
        headers=superuser_token_headers,
    )
    assert r.status_code == 201, r.text
    ns = r.json()
    return ns["id"]


def test_create_agent_and_draft(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "my-agent", "name": "My Agent"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    assert agent["slug"] == "my-agent"

    # Draft auto-created at revision 1.
    r = client.get(f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers)
    assert r.status_code == 200
    draft = r.json()
    assert draft["revision"] == 1
    assert draft["validation_status"] == "unvalidated"


def test_duplicate_slug_409(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    body = {"slug": "dup", "name": "First"}
    r = client.post(f"{settings.API_V1_STR}/agents", json=body, headers=headers)
    assert r.status_code == 201
    r = client.post(f"{settings.API_V1_STR}/agents", json=body, headers=headers)
    assert r.status_code == 409


def test_slug_immutable(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "s1", "name": "S1"},
        headers=headers,
    )
    agent_id = r.json()["id"]
    # AgentUpdate schema has no slug field, so PATCH cannot change it.
    r = client.patch(
        f"{settings.API_V1_STR}/agents/{agent_id}",
        json={"slug": "s2", "name": "S2"},
        headers=headers,
    )
    assert r.status_code == 422  # extra field rejected by pydantic
    # Verify slug unchanged.
    r = client.get(f"{settings.API_V1_STR}/agents/{agent_id}", headers=headers)
    assert r.json()["slug"] == "s1"


def test_cas_draft_conflict_409(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "cas", "name": "CAS"},
        headers=headers,
    ).json()
    # Save at revision 1.
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "first"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["revision"] == 2
    # Second save at stale revision 1 -> 409.
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "second"},
        headers=headers,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["current_revision"] == 2


def test_draft_save_invalidates_validation(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "inv", "name": "Inv"},
        headers=headers,
    ).json()
    # Validate (will error due to missing model, but sets validation_result).
    client.post(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft/validate",
        headers=headers,
    )
    draft = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers
    ).json()
    assert draft["validation_result"] is not None
    # Save -> invalidates.
    client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": draft["revision"], "system_prompt": "changed"},
        headers=headers,
    )
    draft = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers
    ).json()
    assert draft["validated_revision"] is None
    assert draft["validation_status"] == "stale"


def test_config_rejects_bypass_permissions(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={
            "name": "p1",
            "config": {"permission_mode": "bypassPermissions"},
        },
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "bypass_permissions_forbidden" for e in errs)


def test_config_rejects_denylisted_env(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={
            "name": "p2",
            "config": {"allowed_env_names": ["LD_PRELOAD"]},
        },
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "env_denied" for e in errs)


def test_config_rejects_secret_key(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p3", "config": {"api_key": "sk-xxx"}},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "secret_value_forbidden" for e in errs)


def test_config_rejects_shell_string(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p4", "config": {"some_flag": "ls; rm -rf /"}},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "shell_metachar_forbidden" for e in errs)


def test_unknown_harness_type_rejected(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p5", "harness_type": "codex_cli"},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "unsupported_harness" for e in errs)


def test_profile_referenced_cannot_delete(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    profile = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "pref"},
        headers=headers,
    ).json()
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "a1", "name": "A1"},
        headers=headers,
    ).json()
    client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "harness_profile_id": profile["id"]},
        headers=headers,
    )
    r = client.delete(
        f"{settings.API_V1_STR}/harness-profiles/{profile['id']}", headers=headers
    )
    assert r.status_code == 409


def test_cross_namespace_agent_404(
    client: TestClient, superuser_token_headers
):
    # Create namespace A + agent; query from namespace B.
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={"name": f"nsA-{uuid.uuid4().hex[:6]}", "code": f"nsa-{uuid.uuid4().hex[:6]}", "is_active": True},
        headers=superuser_token_headers,
    )
    ns_a = r.json()["id"]
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={"name": f"nsB-{uuid.uuid4().hex[:6]}", "code": f"nsb-{uuid.uuid4().hex[:6]}", "is_active": True},
        headers=superuser_token_headers,
    )
    ns_b = r.json()["id"]
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "cross", "name": "Cross"},
        headers=_ns_headers(superuser_token_headers, ns_a),
    ).json()
    r = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}",
        headers=_ns_headers(superuser_token_headers, ns_b),
    )
    assert r.status_code == 404


def test_environment_catalog(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.get(f"{settings.API_V1_STR}/harnesses/environment-catalog", headers=headers)
    assert r.status_code == 200
    cat = r.json()
    assert "LD_PRELOAD" in cat["denylist"]
    assert "ANTHROPIC_MODEL" in cat["allowlist"]


def test_harness_catalog(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.get(f"{settings.API_V1_STR}/harnesses/catalog", headers=headers)
    assert r.status_code == 200
    harnesses = r.json()["harnesses"]
    assert any(h["type"] == "claude_code" and h["supported"] for h in harnesses)


def test_archived_agent_cannot_be_edited(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "arc", "name": "Arc"},
        headers=headers,
    ).json()
    client.patch(
        f"{settings.API_V1_STR}/agents/{agent['id']}",
        json={"status": "archived"},
        headers=headers,
    )
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "x"},
        headers=headers,
    )
    assert r.status_code == 409


def test_developer_cannot_write(
    client: TestClient, superuser_token_headers, namespace_setup
):
    # Create a developer user INSIDE the namespace via the namespace user-create
    # endpoint (POST /namespaces/{id}/users creates a new user with email+
    # password+role, it does NOT bind an existing user). Then log in as that
    # developer and assert read=200, write=403.
    from tests.utils.user import user_authentication_headers
    from tests.utils.utils import random_email, random_lower_string

    dev_email = random_email()
    dev_password = random_lower_string()
    r = client.post(
        f"{settings.API_V1_STR}/namespaces/{namespace_setup}/users",
        json={
            "email": dev_email,
            "password": dev_password,
            "full_name": "Dev User",
            "is_active": True,
            "role": "developer",
        },
        headers=_ns_headers(superuser_token_headers, namespace_setup),
    )
    assert r.status_code == 201, r.text
    dev_headers = _ns_headers(
        user_authentication_headers(client=client, email=dev_email, password=dev_password),
        namespace_setup,
    )
    # Read OK.
    r = client.get(f"{settings.API_V1_STR}/agents", headers=dev_headers)
    assert r.status_code == 200
    # Write forbidden (require_namespace_admin -> 403).
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "dev", "name": "Dev"},
        headers=dev_headers,
    )
    assert r.status_code == 403
```

NOTE: The namespace user-create endpoint is `POST /api/v1/namespaces/{namespace_id}/users` and its body schema is `NamespaceUserCreate` (`email`, `password`, `full_name?`, `is_active`, `role`). It creates a NEW user with that role in the namespace — it does not bind an existing user by id. `test_developer_cannot_write` reflects this: it creates a developer-scoped user, logs in, and asserts read=200 / write=403.

- [ ] **Step 4: Run the route tests**

Run: `cd backend && python -m pytest tests/agent_management/test_routes.py -v`
Expected: all PASS. If a namespace member-add endpoint path differs, fix the test to match the real path — do NOT skip the test.

- [ ] **Step 5: Run the full backend test suite to ensure no regressions**

Run: `cd backend && python -m pytest -q`
Expected: all pass (existing tests + new tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add backend/tests/conftest.py backend/tests/agent_management/test_routes.py
git commit -m "test(agent_management): API route tests for permissions, CAS, security"
```

---

## Task 7: Frontend API client methods

**Files:**
- Modify: `frontend/src/api/tenantApi.ts`

**Interfaces:**
- Produces: typed interfaces and `agentsApi` / `harnessProfilesApi` / `harnessCatalogApi` objects in `tenantApi.ts`, matching the backend schemas.

- [ ] **Step 1: Add types and API methods to tenantApi.ts**

In `frontend/src/api/tenantApi.ts`, add these types and API objects (place after the existing runtime exports, before the closing of the file):

```ts
export interface AgentDefinition {
  id: string
  namespace_id: string
  slug: string
  name: string
  description: string | null
  status: "active" | "archived"
  created_at: string
  updated_at: string
}

export interface AgentListItem extends AgentDefinition {
  draft_revision: number
  validation_status: "unvalidated" | "validated" | "stale" | "error"
  harness_type: string | null
  model_id: string | null
}

export interface AgentDraftPublic {
  agent_id: string
  revision: number
  harness_profile_id: string | null
  provider_config_id: string | null
  model_id: string | null
  system_prompt: string
  config: Record<string, unknown>
  validated_revision: number | null
  validation_result: Record<string, unknown> | null
  validation_status: "unvalidated" | "validated" | "stale" | "error"
  updated_at: string
}

export interface HarnessProfilePublic {
  id: string
  namespace_id: string
  name: string
  harness_type: string
  config_schema_version: string
  cli_version_constraint: string
  sdk_version_constraint: string
  config: Record<string, unknown>
  archived: boolean
  referenced_by_agents: boolean
  created_at: string
  updated_at: string
}

export interface HarnessCatalogField {
  name: string
  type: string
  allowed?: string[]
  max?: number
  allowlist?: string[]
}

export interface HarnessCatalogItem {
  type: string
  config_schema_version: string
  supported: boolean
  description: string
  fields: HarnessCatalogField[]
}

export interface EnvironmentCatalog {
  allowlist: string[]
  reserved: string[]
  denylist: string[]
}

export interface Diagnostic {
  code: string
  field: string
  message: string
}

export interface TargetCompatibility {
  runtime_profile_id: string
  runtime_type: string
  cli_version: string | null
  sdk_version: string | null
  compatible: boolean | null
  reason: string | null
}

export interface ValidationResult {
  validated_revision: number
  status: string
  errors: Diagnostic[]
  warnings: Diagnostic[]
  target_compatibility: TargetCompatibility[]
}

export const agentsApi = {
  list: async () => {
    const { data } = await api.get<{ data: AgentListItem[]; count: number }>(
      "/api/v1/agents"
    )
    return data
  },
  create: async (body: { slug: string; name: string; description?: string }) => {
    const { data } = await api.post<AgentDefinition>("/api/v1/agents", body)
    return data
  },
  get: async (agentId: string) => {
    const { data } = await api.get<AgentDefinition>(`/api/v1/agents/${agentId}`)
    return data
  },
  update: async (
    agentId: string,
    body: { name?: string; description?: string; status?: "active" | "archived" }
  ) => {
    const { data } = await api.patch<AgentDefinition>(
      `/api/v1/agents/${agentId}`,
      body
    )
    return data
  },
  delete: async (agentId: string) => {
    await api.delete(`/api/v1/agents/${agentId}`)
  },
  getDraft: async (agentId: string) => {
    const { data } = await api.get<AgentDraftPublic>(
      `/api/v1/agents/${agentId}/draft`
    )
    return data
  },
  saveDraft: async (
    agentId: string,
    body: {
      expected_revision: number
      harness_profile_id?: string | null
      provider_config_id?: string | null
      model_id?: string | null
      system_prompt?: string
      config?: Record<string, unknown>
    }
  ) => {
    const { data } = await api.put<AgentDraftPublic>(
      `/api/v1/agents/${agentId}/draft`,
      body
    )
    return data
  },
  validate: async (agentId: string) => {
    const { data } = await api.post<ValidationResult>(
      `/api/v1/agents/${agentId}/draft/validate`
    )
    return data
  },
}

export const harnessProfilesApi = {
  list: async () => {
    const { data } = await api.get<{ data: HarnessProfilePublic[]; count: number }>(
      "/api/v1/harness-profiles"
    )
    return data
  },
  create: async (body: {
    name: string
    harness_type?: string
    config_schema_version?: string
    cli_version_constraint?: string
    sdk_version_constraint?: string
    config?: Record<string, unknown>
  }) => {
    const { data } = await api.post<HarnessProfilePublic>(
      "/api/v1/harness-profiles",
      body
    )
    return data
  },
  get: async (profileId: string) => {
    const { data } = await api.get<HarnessProfilePublic>(
      `/api/v1/harness-profiles/${profileId}`
    )
    return data
  },
  update: async (
    profileId: string,
    body: {
      name?: string
      cli_version_constraint?: string
      sdk_version_constraint?: string
      config?: Record<string, unknown>
      archived?: boolean
    }
  ) => {
    const { data } = await api.patch<HarnessProfilePublic>(
      `/api/v1/harness-profiles/${profileId}`,
      body
    )
    return data
  },
  delete: async (profileId: string) => {
    await api.delete(`/api/v1/harness-profiles/${profileId}`)
  },
}

export const harnessCatalogApi = {
  harnesses: async () => {
    const { data } = await api.get<{ harnesses: HarnessCatalogItem[] }>(
      "/api/v1/harnesses/catalog"
    )
    return data.harnesses
  },
  environment: async () => {
    const { data } = await api.get<EnvironmentCatalog>(
      "/api/v1/harnesses/environment-catalog"
    )
    return data
  },
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: no new errors from the added code (pre-existing errors may exist; only check the new lines compile).

- [ ] **Step 3: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add frontend/src/api/tenantApi.ts
git commit -m "feat(frontend): typed API client for agents and harness profiles"
```

---

## Task 8: Agent list page + sidebar entry

**Files:**
- Create: `frontend/src/routes/_layout/system.agents.tsx`
- Create: `frontend/src/components/Agents/AgentList.tsx`
- Create: `frontend/src/components/Agents/CreateAgentDialog.tsx`
- Modify: `frontend/src/components/Sidebar/Main.tsx` (or the sidebar `baseItems`/`items` builder) — add Agent 管理 entry.

**Interfaces:**
- Consumes: `agentsApi`, `AgentListItem` from tenantApi; `useAuth` for role gating; existing shadcn/ui components (Card, Table, Dialog, Button, Input, Badge).

- [ ] **Step 1: Create the route file**

Create `frontend/src/routes/_layout/system.agents.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router"
import AgentList from "@/components/Agents/AgentList"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/agents")({
  component: AgentsPage,
})

function AgentsPage() {
  const { user } = useAuth()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  if (!namespaceId)
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent>请先选择空间。</CardContent>
      </Card>
    )
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId
  )?.role
  const visible = Boolean(
    user?.is_superuser || role === "admin" || role === "developer"
  )
  if (user && !visible) return null
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent 管理</h1>
        <p className="text-muted-foreground">
          管理空间级 Agent 定义、草稿与 Claude Harness 配置。
        </p>
      </div>
      <AgentList canManage={canManage} />
    </div>
  )
}
```

- [ ] **Step 2: Create CreateAgentDialog component**

Create `frontend/src/components/Agents/CreateAgentDialog.tsx`:

```tsx
import { useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { agentsApi } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function CreateAgentDialog() {
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () =>
      agentsApi.create({ slug, name, description: description || undefined }),
    onSuccess: (agent) => {
      showSuccessToast("Agent 已创建")
      setOpen(false)
      setSlug("")
      setName("")
      setDescription("")
      queryClient.invalidateQueries({ queryKey: ["agents"] })
      navigate({ to: "/system/agents/$agentId", params: { agentId: agent.id } })
    },
    onError: handleError,
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>创建 Agent</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>创建 Agent</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="agent-slug">Slug</Label>
            <Input
              id="agent-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-agent"
            />
            <p className="text-xs text-muted-foreground">
              空间内唯一，创建后不可修改。仅小写字母、数字、连字符。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="agent-name">名称</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="agent-desc">说明</Label>
            <Input
              id="agent-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!slug || !name || mutation.isPending}
          >
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 3: Create AgentList component**

Create `frontend/src/components/Agents/AgentList.tsx`:

```tsx
import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { agentsApi, type AgentListItem } from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import CreateAgentDialog from "./CreateAgentDialog"

const statusColor: Record<string, string> = {
  validated: "bg-green-100 text-green-800",
  unvalidated: "bg-gray-100 text-gray-800",
  stale: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
}

export default function AgentList({ canManage }: { canManage: boolean }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: agentsApi.list,
  })

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Agent 列表</CardTitle>
        {canManage && <CreateAgentDialog />}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-muted-foreground">加载中…</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>Slug</TableHead>
                <TableHead>Harness</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>草稿修订</TableHead>
                <TableHead>校验状态</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.data.map((agent: AgentListItem) => (
                <TableRow key={agent.id}>
                  <TableCell className="font-medium">
                    <Link
                      to="/system/agents/$agentId"
                      params={{ agentId: agent.id }}
                      className="text-blue-600 hover:underline"
                    >
                      {agent.name}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-sm">{agent.slug}</TableCell>
                  <TableCell>{agent.harness_type ?? "—"}</TableCell>
                  <TableCell>{agent.model_id ?? "—"}</TableCell>
                  <TableCell>r{agent.draft_revision}</TableCell>
                  <TableCell>
                    <Badge className={statusColor[agent.validation_status] ?? ""}>
                      {agent.validation_status}
                    </Badge>
                  </TableCell>
                  <TableCell>{agent.status}</TableCell>
                </TableRow>
              ))}
              {data?.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    暂无 Agent
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 4: Add sidebar entries**

Modify `frontend/src/components/Sidebar/Main.tsx` (the file exporting `Main` and `Item`). Find where the `items` array is built in `AppSidebar`. The existing `Item` type already supports `children`. Add an "Agent 管理" item with children when `canUseRuntimes` is true. In `AppSidebar`, after the runtimes push, add:

```tsx
      if (canUseRuntimes) {
        items.push({
          icon: Cpu,
          title: "运行时管理",
          path: "/system/runtimes",
        })
        items.push({
          icon: Sparkles,
          title: "Agent 管理",
          children: [
            { title: "Agent", path: "/system/agents" },
            { title: "Harness 配置", path: "/system/harnesses" },
          ],
        })
      }
```

Import `Sparkles` is already imported in that file (used for llm-providers). If `Sparkles` is already used for llm-providers, use a different icon for Agent 管理, e.g. `Bot` from lucide-react. Add `Bot` to the import line: `import { Briefcase, Bot, Building2, Cpu, Home, Sparkles, Users } from "lucide-react"` and use `Bot` for the Agent 管理 item.

Also confirm `Main.tsx` renders `children` — if it currently only renders flat items, extend it to render a collapsible sub-group. Check the existing `Main` component: if it ignores `children`, add a nested `SidebarMenu` rendering for items that have `children`.

- [ ] **Step 5: Verify the frontend compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add frontend/src/routes/_layout/system.agents.tsx frontend/src/components/Agents/ \
  frontend/src/components/Sidebar/Main.tsx
git commit -m "feat(frontend): agent list page and sidebar entry"
```

---

## Task 9: Agent editor page

**Files:**
- Create: `frontend/src/routes/_layout/system.agents.$agentId.tsx`
- Create: `frontend/src/components/Agents/AgentEditor.tsx`

**Interfaces:**
- Consumes: `agentsApi`, `harnessProfilesApi`, `llmProviderConfigsApi` (existing) for model selection; `useAuth` for role gating; shadcn/ui Tabs, Textarea, Select, Button.

- [ ] **Step 1: Create the route file**

Create `frontend/src/routes/_layout/system.agents.$agentId.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router"
import AgentEditor from "@/components/Agents/AgentEditor"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/system/agents/$agentId")({
  component: AgentEditorPage,
})

function AgentEditorPage() {
  const { agentId } = Route.useParams()
  const { user } = useAuth()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  if (!namespaceId)
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent>请先选择空间。</CardContent>
      </Card>
    )
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId
  )?.role
  const canManage = Boolean(user?.is_superuser || role === "admin")
  return <AgentEditor agentId={agentId} canManage={canManage} />
}
```

- [ ] **Step 2: Create AgentEditor component**

Create `frontend/src/components/Agents/AgentEditor.tsx`. This component has 5 tabs (概览/模型与提示词/Harness/能力/校验与发布), with the last two disabled as placeholders. It loads the agent + draft, supports CAS save with 409 reload handling, and validation triggering.

```tsx
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  agentsApi,
  harnessProfilesApi,
  type AgentDraftPublic,
  type ValidationResult,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function AgentEditor({
  agentId,
  canManage,
}: {
  agentId: string
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { data: agent } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId),
  })
  const { data: draft, refetch: refetchDraft } = useQuery({
    queryKey: ["agent-draft", agentId],
    queryFn: () => agentsApi.getDraft(agentId),
  })
  const { data: profiles } = useQuery({
    queryKey: ["harness-profiles"],
    queryFn: harnessProfilesApi.list,
  })

  // Local editable state, initialized from draft.
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null)
  const [harnessProfileId, setHarnessProfileId] = useState<string>("")
  const [systemPrompt, setSystemPrompt] = useState("")
  const [modelId, setModelId] = useState("")
  const [providerConfigId, setProviderConfigId] = useState("")
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (draft) {
      setExpectedRevision(draft.revision)
      setHarnessProfileId(draft.harness_profile_id ?? "")
      setSystemPrompt(draft.system_prompt)
      setModelId(draft.model_id ?? "")
      setProviderConfigId(draft.provider_config_id ?? "")
      setDirty(false)
    }
  }, [draft])

  const saveMutation = useMutation({
    mutationFn: () =>
      agentsApi.saveDraft(agentId, {
        expected_revision: expectedRevision!,
        harness_profile_id: harnessProfileId || null,
        provider_config_id: providerConfigId || null,
        model_id: modelId || null,
        system_prompt: systemPrompt,
      }),
    onSuccess: () => {
      showSuccessToast("草稿已保存")
      setDirty(false)
      refetchDraft()
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: (err) => {
      // 409 conflict: offer reload.
      handleError(err)
    },
  })

  const validateMutation = useMutation({
    mutationFn: () => agentsApi.validate(agentId),
    onSuccess: (result: ValidationResult) => {
      if (result.status === "validated") {
        showSuccessToast("校验通过")
      } else {
        showErrorToast(
          `校验失败：${result.errors.length} 个错误，${result.warnings.length} 个警告`
        )
      }
      refetchDraft()
      queryClient.invalidateQueries({ queryKey: ["agents"] })
    },
    onError: handleError,
  })

  const reload = () => refetchDraft()

  if (!agent || !draft) return <p className="text-muted-foreground">加载中…</p>

  const archived = agent.status === "archived"

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{agent.name}</h1>
          <p className="text-sm text-muted-foreground font-mono">{agent.slug}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge>revision {draft.revision}</Badge>
          <Badge>{draft.validation_status}</Badge>
          {agent.status === "archived" && <Badge variant="destructive">已归档</Badge>}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="model">模型与提示词</TabsTrigger>
          <TabsTrigger value="harness">Harness</TabsTrigger>
          <TabsTrigger value="capabilities" disabled>
            能力
          </TabsTrigger>
          <TabsTrigger value="publish" disabled>
            校验与发布
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle>概览</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>名称</Label>
                <Input
                  value={agent.name}
                  disabled={!canManage || archived}
                  onChange={() => {}}
                />
              </div>
              <div className="space-y-2">
                <Label>说明</Label>
                <Input
                  value={agent.description ?? ""}
                  disabled={!canManage || archived}
                  onChange={() => {}}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                名称与说明请通过保存草稿外的编辑入口修改（概览编辑在后续完善）。
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="model">
          <Card>
            <CardHeader>
              <CardTitle>模型与系统提示词</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Provider 配置</Label>
                <Input
                  value={providerConfigId}
                  onChange={(e) => {
                    setProviderConfigId(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  placeholder="provider config UUID"
                />
              </div>
              <div className="space-y-2">
                <Label>模型 ID</Label>
                <Input
                  value={modelId}
                  onChange={(e) => {
                    setModelId(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  placeholder="claude-sonnet-4-20250514"
                />
              </div>
              <div className="space-y-2">
                <Label>系统提示词</Label>
                <textarea
                  className="min-h-40 w-full rounded-md border bg-background p-3"
                  value={systemPrompt}
                  onChange={(e) => {
                    setSystemPrompt(e.target.value)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                  rows={10}
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="harness">
          <Card>
            <CardHeader>
              <CardTitle>Harness Profile</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Harness Profile</Label>
                <Select
                  value={harnessProfileId}
                  onValueChange={(v) => {
                    setHarnessProfileId(v)
                    setDirty(true)
                  }}
                  disabled={!canManage || archived}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择 Harness Profile" />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles?.data
                      .filter((p) => !p.archived)
                      .map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name} ({p.harness_type})
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="capabilities">
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              能力页签将在后续阶段启用（Skill / Tool / MCP / Plugin 绑定）。
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="publish">
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              校验与发布将在后续阶段启用（ResolvedAgentSpec / Agent Release）。
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {canManage && !archived && (
        <div className="flex items-center gap-2">
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!dirty || saveMutation.isPending}
          >
            保存草稿
          </Button>
          <Button
            variant="secondary"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
          >
            校验草稿
          </Button>
          <Button variant="ghost" onClick={reload}>
            重新加载
          </Button>
          {dirty && (
            <span className="text-sm text-yellow-600">有未保存的修改</span>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Verify compile**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add frontend/src/routes/_layout/system.agents.\$agentId.tsx frontend/src/components/Agents/AgentEditor.tsx
git commit -m "feat(frontend): agent editor page with tabs and CAS save"
```

---

## Task 10: Harness config page

**Files:**
- Create: `frontend/src/routes/_layout/system.harnesses.tsx`
- Create: `frontend/src/components/Agents/HarnessProfileSheet.tsx`

**Interfaces:**
- Consumes: `harnessProfilesApi`, `harnessCatalogApi`; shadcn/ui Sheet, Select, Input, Badge.

- [ ] **Step 1: Create the route file**

Create `frontend/src/routes/_layout/system.harnesses.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router"
import HarnessProfileSheet from "@/components/Agents/HarnessProfileSheet"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import useAuth from "@/hooks/useAuth"
import { useQuery } from "@tanstack/react-query"
import { harnessProfilesApi, type HarnessProfilePublic } from "@/api/tenantApi"
import { useState } from "react"

export const Route = createFileRoute("/_layout/system/harnesses")({
  component: HarnessesPage,
})

function HarnessesPage() {
  const { user } = useAuth()
  const namespaceId = localStorage.getItem("selected_namespace_id")
  const [editing, setEditing] = useState<HarnessProfilePublic | null>(null)
  const [creating, setCreating] = useState(false)
  if (!namespaceId)
    return (
      <Card>
        <CardHeader>
          <CardTitle>未选择空间</CardTitle>
        </CardHeader>
        <CardContent>请先选择空间。</CardContent>
      </Card>
    )
  const role = user?.namespace_roles?.find(
    (item) => item.namespace_id === namespaceId
  )?.role
  const visible = Boolean(
    user?.is_superuser || role === "admin" || role === "developer"
  )
  if (user && !visible) return null
  const canManage = Boolean(user?.is_superuser || role === "admin")

  const { data, isLoading } = useQuery({
    queryKey: ["harness-profiles"],
    queryFn: harnessProfilesApi.list,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Harness 配置</h1>
          <p className="text-muted-foreground">
            管理可复用的 Claude Harness Profile 与结构化 CLI 配置。
          </p>
        </div>
        {canManage && (
          <Button onClick={() => setCreating(true)}>创建 Profile</Button>
        )}
      </div>
      <Card>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground">加载中…</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>Harness</TableHead>
                  <TableHead>CLI 约束</TableHead>
                  <TableHead>SDK 约束</TableHead>
                  <TableHead>引用</TableHead>
                  <TableHead>状态</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.data.map((p) => (
                  <TableRow
                    key={p.id}
                    onClick={() => setEditing(p)}
                    className="cursor-pointer"
                  >
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell>{p.harness_type}</TableCell>
                    <TableCell className="font-mono text-sm">
                      {p.cli_version_constraint}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {p.sdk_version_constraint}
                    </TableCell>
                    <TableCell>
                      {p.referenced_by_agents ? "被引用" : "—"}
                    </TableCell>
                    <TableCell>
                      {p.archived ? (
                        <Badge variant="secondary">已归档</Badge>
                      ) : (
                        <Badge>启用</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {(creating || editing) && (
        <HarnessProfileSheet
          profile={editing}
          open={creating || editing !== null}
          onOpenChange={(o) => {
            if (!o) {
              setCreating(false)
              setEditing(null)
            }
          }}
          canManage={canManage}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create HarnessProfileSheet component**

Create `frontend/src/components/Agents/HarnessProfileSheet.tsx`:

```tsx
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  harnessProfilesApi,
  harnessCatalogApi,
  type HarnessProfilePublic,
  type EnvironmentCatalog,
} from "@/api/tenantApi"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export default function HarnessProfileSheet({
  profile,
  open,
  onOpenChange,
  canManage,
}: {
  profile: HarnessProfilePublic | null
  open: boolean
  onOpenChange: (open: boolean) => void
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast } = useCustomToast()
  const { data: envCatalog } = useQuery<EnvironmentCatalog>({
    queryKey: ["env-catalog"],
    queryFn: harnessCatalogApi.environment,
  })

  const [name, setName] = useState("")
  const [cliConstraint, setCliConstraint] = useState(">=1.0.0")
  const [sdkConstraint, setSdkConstraint] = useState(">=0.2.0")
  const [permissionMode, setPermissionMode] = useState("default")
  const [timeoutSeconds, setTimeoutSeconds] = useState(3600)
  const [allowedEnv, setAllowedEnv] = useState<string[]>([])

  useEffect(() => {
    if (profile) {
      setName(profile.name)
      setCliConstraint(profile.cli_version_constraint)
      setSdkConstraint(profile.sdk_version_constraint)
      setPermissionMode(
        (profile.config as Record<string, unknown>).permission_mode as string ??
          "default"
      )
      setTimeoutSeconds(
        (profile.config as Record<string, unknown>).timeout_seconds as number ??
          3600
      )
      setAllowedEnv(
        ((profile.config as Record<string, unknown>).allowed_env_names as string[]) ??
          []
      )
    } else {
      setName("")
      setCliConstraint(">=1.0.0")
      setSdkConstraint(">=0.2.0")
      setPermissionMode("default")
      setTimeoutSeconds(3600)
      setAllowedEnv([])
    }
  }, [profile])

  const buildConfig = () => ({
    permission_mode: permissionMode,
    timeout_seconds: timeoutSeconds,
    allowed_env_names: allowedEnv,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      harnessProfilesApi.create({
        name,
        cli_version_constraint: cliConstraint,
        sdk_version_constraint: sdkConstraint,
        config: buildConfig(),
      }),
    onSuccess: () => {
      showSuccessToast("Profile 已创建")
      queryClient.invalidateQueries({ queryKey: ["harness-profiles"] })
      onOpenChange(false)
    },
    onError: handleError,
  })

  const updateMutation = useMutation({
    mutationFn: () =>
      harnessProfilesApi.update(profile!.id, {
        name,
        cli_version_constraint: cliConstraint,
        sdk_version_constraint: sdkConstraint,
        config: buildConfig(),
      }),
    onSuccess: () => {
      showSuccessToast("Profile 已更新")
      queryClient.invalidateQueries({ queryKey: ["harness-profiles"] })
      onOpenChange(false)
    },
    onError: handleError,
  })

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{profile ? "编辑 Profile" : "创建 Profile"}</SheetTitle>
        </SheetHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>名称</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>Harness 类型</Label>
            <Input value="claude_code" disabled />
            <p className="text-xs text-muted-foreground">
              v0.5 仅支持 claude_code。
            </p>
          </div>
          <div className="space-y-2">
            <Label>CLI 版本约束</Label>
            <Input
              value={cliConstraint}
              onChange={(e) => setCliConstraint(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>SDK 版本约束</Label>
            <Input
              value={sdkConstraint}
              onChange={(e) => setSdkConstraint(e.target.value)}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>权限模式</Label>
            <Select
              value={permissionMode}
              onValueChange={setPermissionMode}
              disabled={!canManage}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">default</SelectItem>
                <SelectItem value="acceptEdits">acceptEdits</SelectItem>
                <SelectItem value="plan">plan</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              bypassPermissions 不可选，由后端强制拒绝。
            </p>
          </div>
          <div className="space-y-2">
            <Label>超时（秒）</Label>
            <Input
              type="number"
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
              disabled={!canManage}
            />
          </div>
          <div className="space-y-2">
            <Label>允许的环境变量</Label>
            <Select
              value=""
              onValueChange={(v) => {
                if (!allowedEnv.includes(v)) setAllowedEnv([...allowedEnv, v])
              }}
              disabled={!canManage}
            >
              <SelectTrigger>
                <SelectValue placeholder="从 allowlist 选择" />
              </SelectTrigger>
              <SelectContent>
                {envCatalog?.allowlist
                  .filter((e) => !allowedEnv.includes(e))
                  .map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <div className="flex flex-wrap gap-1">
              {allowedEnv.map((e) => (
                <span
                  key={e}
                  className="rounded bg-gray-100 px-2 py-0.5 text-xs"
                  onClick={() =>
                    canManage && setAllowedEnv(allowedEnv.filter((x) => x !== e))
                  }
                >
                  {e} ✕
                </span>
              ))}
            </div>
          </div>
        </div>
        <SheetFooter>
          {canManage && (
            <Button
              onClick={() =>
                profile ? updateMutation.mutate() : createMutation.mutate()
              }
              disabled={!name || createMutation.isPending || updateMutation.isPending}
            >
              {profile ? "保存" : "创建"}
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
```

- [ ] **Step 3: Verify compile**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/suyaqi/Desktop/NeoMua
git add frontend/src/routes/_layout/system.harnesses.tsx frontend/src/components/Agents/HarnessProfileSheet.tsx
git commit -m "feat(frontend): harness profile config page"
```

---

## Task 11: End-to-end verification

**Files:**
- None (verification only).

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors introduced by this work.

- [ ] **Step 3: Run frontend lint**

Run: `cd frontend && npm run lint 2>&1 | tail -20` (or the project's lint command — check package.json).
Expected: no new errors.

- [ ] **Step 4: Manual smoke test (backend)**

Start the stack and exercise the key path as superuser:
1. Create a namespace.
2. `POST /api/v1/agents` with `X-Namespace-Id`.
3. `GET /api/v1/agents/{id}/draft` — revision 1.
4. `POST /api/v1/harness-profiles` with a valid config.
5. `PUT /api/v1/agents/{id}/draft` binding the profile + system prompt.
6. `POST /api/v1/agents/{id}/draft/validate` — expect errors (no model configured) but structured result.
7. Confirm `bypassPermissions` config is rejected with 422.

- [ ] **Step 5: Verify acceptance criteria mapping**

Confirm each PRD §7 acceptance criterion is covered:
- Admin CRUD / Developer read / User denied → `test_developer_cannot_write`, role gating in routes.
- CAS 409 → `test_cas_draft_conflict_409`.
- Cross-namespace/invalid model/unknown harness blocks → `test_cross_namespace_agent_404`, `test_unknown_harness_type_rejected`, model validation in service.
- bypassPermissions/Shell/secret/denylist env rejected → catalog tests + route tests.
- unknown target version blocks → service `target_version_unknown` error.
- draft change invalidates validation → `test_draft_save_invalidates_validation`.
- archived agent cannot edit → `test_archived_agent_cannot_be_edited`.
- v0.5 only claude_code → `test_unknown_harness_type_rejected` + harness catalog.

- [ ] **Step 6: Final commit if any fixes were made**

If verification surfaced fixes, commit them. Otherwise no-op.

---

## Notes for implementer

- The `require_namespace_admin` dep already enforces admin role (returns namespace_id or raises 403). The additional `_require_admin` helper in routes is belt-and-suspenders for superuser bypass correctness — keep it.
- `AgentDraft.system_prompt` defaults to `""` (empty) and is non-null; the create flow makes an empty draft, validation will flag `system_prompt_empty`.
- The migration uses `postgresql.JSONB`. SQLModel's `Column(JSON)` maps to `JSONB` on PostgreSQL — verify the existing migrations use the same (they do, via `postgresql.JSONB`).
- Frontend file route for `$agentId` uses TanStack's `$` param syntax; escape the `$` in git add with `\$`.
- If `src/components/ui/sheet.tsx` does not exist, add it via shadcn: `npx shadcn@latest add sheet` (check the project's shadcn setup first). Same for `tabs`, `select`, `textarea`, `badge`, `table` if missing — but they likely exist given the existing runtime pages.

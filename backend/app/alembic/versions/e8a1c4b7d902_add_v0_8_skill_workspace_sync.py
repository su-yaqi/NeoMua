"""add v0.8 Skill workspace and runtime synchronization

Revision ID: e8a1c4b7d902
Revises: c4d8a2f7e106
Create Date: 2026-07-17 00:00:00
"""

import json

import sqlalchemy as sa
from alembic import op
from packaging.version import InvalidVersion, Version

revision = "e8a1c4b7d902"
down_revision = "c4d8a2f7e106"
branch_labels = None
depends_on = None


def _backfill_current_versions() -> None:
    connection = op.get_bind()
    skill_ids = connection.execute(sa.text("SELECT id FROM skill_definition")).scalars()
    for skill_id in skill_ids:
        rows = connection.execute(
            sa.text(
                "SELECT id, version FROM skill_version "
                "WHERE skill_id = :skill_id AND deprecated = false"
            ),
            {"skill_id": skill_id},
        ).all()
        if not rows:
            raise RuntimeError(
                f"Skill {skill_id} has no active version; archive or repair it before v0.8"
            )
        try:
            selected = max(rows, key=lambda row: Version(str(row.version)))
        except InvalidVersion as exc:
            raise RuntimeError(
                f"Skill {skill_id} contains a non-SemVer version and cannot be migrated"
            ) from exc
        connection.execute(
            sa.text(
                "UPDATE skill_definition SET current_version_id = :version_id "
                "WHERE id = :skill_id"
            ),
            {"version_id": selected.id, "skill_id": skill_id},
        )


def _migrate_plugin_drafts(*, downgrade: bool = False) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, components, adapter_schema_version FROM plugin_draft")
    ).all()
    for draft_id, raw_components, adapter_schema_version in rows:
        components = raw_components or []
        target_schema = "1.0" if downgrade else "1.1"
        changed = adapter_schema_version != target_schema
        normalized = []
        for raw in components:
            item = dict(raw)
            if item.get("type") != "skill":
                normalized.append(item)
                continue
            if downgrade:
                skill_id = item.get("skill_id")
                version_id = connection.execute(
                    sa.text(
                        "SELECT current_version_id FROM skill_definition WHERE id = :skill_id"
                    ),
                    {"skill_id": skill_id},
                ).scalar_one_or_none()
                if version_id is None:
                    raise RuntimeError(
                        f"Plugin draft {draft_id} references an unavailable Skill"
                    )
                item["skill_version_id"] = str(version_id)
                changed = item.pop("skill_id", None) is not None or changed
            elif item.get("skill_version_id"):
                skill_id = connection.execute(
                    sa.text(
                        "SELECT skill_id FROM skill_version WHERE id = :version_id"
                    ),
                    {"version_id": item["skill_version_id"]},
                ).scalar_one_or_none()
                if skill_id is None:
                    raise RuntimeError(
                        f"Plugin draft {draft_id} references an unavailable Skill Version"
                    )
                item = {
                    key: value
                    for key, value in item.items()
                    if key not in {"skill_version_id", "version", "digest"}
                }
                item["skill_id"] = str(skill_id)
                changed = True
            normalized.append(item)
        if changed:
            connection.execute(
                sa.text(
                    "UPDATE plugin_draft SET components = CAST(:components AS JSON), "
                    "adapter_schema_version = :adapter_schema_version, "
                    "revision = revision + 1, validated_revision = NULL, "
                    "validation_result = NULL WHERE id = :draft_id"
                ),
                {
                    "components": json.dumps(normalized),
                    "adapter_schema_version": target_schema,
                    "draft_id": draft_id,
                },
            )


def upgrade() -> None:
    op.add_column(
        "skill_version", sa.Column("manifest_digest", sa.String(64), nullable=True)
    )
    op.add_column("skill_version", sa.Column("signature", sa.Text(), nullable=True))
    op.add_column(
        "skill_version", sa.Column("signing_public_key", sa.Text(), nullable=True)
    )
    op.add_column(
        "skill_version",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE skill_version SET published_at = created_at")

    op.add_column(
        "skill_definition", sa.Column("current_version_id", sa.Uuid(), nullable=True)
    )
    op.add_column("skill_definition", sa.Column("draft_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_skill_definition_current_version_id",
        "skill_definition",
        ["current_version_id"],
    )
    op.create_index("ix_skill_definition_draft_id", "skill_definition", ["draft_id"])
    op.create_foreign_key(
        "fk_skill_definition_current_version_id",
        "skill_definition",
        "skill_version",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )
    _backfill_current_versions()

    op.create_table(
        "skill_draft",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.Uuid(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("validated_revision", sa.Integer(), nullable=True),
        sa.Column("validation_digest", sa.String(64), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["base_version_id"], ["skill_version.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", name="uq_skill_draft_skill_id"),
    )
    op.create_index("ix_skill_draft_skill_id", "skill_draft", ["skill_id"])
    op.create_table(
        "skill_draft_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("is_text", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["skill_draft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "path", name="uq_skill_draft_file_path"),
    )
    op.create_index("ix_skill_draft_file_draft_id", "skill_draft_file", ["draft_id"])
    op.create_index(
        "ix_skill_draft_file_content_sha256",
        "skill_draft_file",
        ["content_sha256"],
    )
    op.create_foreign_key(
        "fk_skill_definition_draft_id",
        "skill_definition",
        "skill_draft",
        ["draft_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_table(
        "skill_current_version_change",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("from_version_id", sa.Uuid(), nullable=True),
        sa.Column("to_version_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["from_version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["changed_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "skill_id", "idempotency_key", name="uq_skill_current_version_change_key"
        ),
    )
    op.create_index(
        "ix_skill_current_version_change_skill_id",
        "skill_current_version_change",
        ["skill_id"],
    )

    op.add_column(
        "agent_draft_skill",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("agent_draft_skill", "skill_version_id", nullable=True)
    _migrate_plugin_drafts()

    op.create_table(
        "runtime_skill_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_profile_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("desired_version_id", sa.Uuid(), nullable=True),
        sa.Column("desired_digest", sa.String(64), nullable=True),
        sa.Column("applied_version_id", sa.Uuid(), nullable=True),
        sa.Column("applied_digest", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("applied_generation", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("subscription_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"], ["runtime_profile.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill_definition.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["desired_version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["applied_version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_profile_id", "skill_id", name="uq_runtime_skill_state"
        ),
    )
    for column in ("namespace_id", "runtime_profile_id", "skill_id", "status"):
        op.create_index(
            f"ix_runtime_skill_state_{column}", "runtime_skill_state", [column]
        )
    op.create_table(
        "runtime_skill_sync_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_skill_state_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("bytes_downloaded", sa.Integer(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["runtime_skill_state_id"], ["runtime_skill_state.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_skill_state_id", "attempt_no", name="uq_runtime_skill_attempt"
        ),
    )
    op.create_index(
        "ix_runtime_skill_sync_attempt_state_id",
        "runtime_skill_sync_attempt",
        ["runtime_skill_state_id"],
    )
    op.create_table(
        "agent_task_skill_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("runtime_generation", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill_definition.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["skill_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_agent_task_skill_usage"),
    )
    op.create_index(
        "ix_agent_task_skill_usage_task_id", "agent_task_skill_usage", ["task_id"]
    )


def downgrade() -> None:
    _migrate_plugin_drafts(downgrade=True)
    op.drop_index(
        "ix_agent_task_skill_usage_task_id", table_name="agent_task_skill_usage"
    )
    op.drop_table("agent_task_skill_usage")
    op.drop_index(
        "ix_runtime_skill_sync_attempt_state_id",
        table_name="runtime_skill_sync_attempt",
    )
    op.drop_table("runtime_skill_sync_attempt")
    for column in ("status", "skill_id", "runtime_profile_id", "namespace_id"):
        op.drop_index(
            f"ix_runtime_skill_state_{column}", table_name="runtime_skill_state"
        )
    op.drop_table("runtime_skill_state")
    op.execute(
        "UPDATE agent_draft_skill AS binding "
        "SET skill_version_id = skill.current_version_id "
        "FROM skill_definition AS skill "
        "WHERE binding.skill_id = skill.id AND binding.skill_version_id IS NULL"
    )
    op.alter_column("agent_draft_skill", "skill_version_id", nullable=False)
    op.drop_column("agent_draft_skill", "enabled")
    op.drop_index(
        "ix_skill_current_version_change_skill_id",
        table_name="skill_current_version_change",
    )
    op.drop_table("skill_current_version_change")
    op.drop_constraint(
        "fk_skill_definition_draft_id", "skill_definition", type_="foreignkey"
    )
    op.drop_index("ix_skill_draft_file_content_sha256", table_name="skill_draft_file")
    op.drop_index("ix_skill_draft_file_draft_id", table_name="skill_draft_file")
    op.drop_table("skill_draft_file")
    op.drop_index("ix_skill_draft_skill_id", table_name="skill_draft")
    op.drop_table("skill_draft")
    op.drop_constraint(
        "fk_skill_definition_current_version_id",
        "skill_definition",
        type_="foreignkey",
    )
    op.drop_index("ix_skill_definition_draft_id", table_name="skill_definition")
    op.drop_index(
        "ix_skill_definition_current_version_id", table_name="skill_definition"
    )
    op.drop_column("skill_definition", "draft_id")
    op.drop_column("skill_definition", "current_version_id")
    op.drop_column("skill_version", "published_at")
    op.drop_column("skill_version", "signing_public_key")
    op.drop_column("skill_version", "signature")
    op.drop_column("skill_version", "manifest_digest")

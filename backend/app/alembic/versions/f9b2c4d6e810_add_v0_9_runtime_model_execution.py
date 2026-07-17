"""add v0.9 Runtime engines, model identities, and execution bindings

Revision ID: f9b2c4d6e810
Revises: e8a1c4b7d902
Create Date: 2026-07-17 00:00:00
"""

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "f9b2c4d6e810"
down_revision = "e8a1c4b7d902"
branch_labels = None
depends_on = None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name)


def _create_model_tables() -> None:
    op.create_table(
        "llm_model_definition",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("provider_family", sa.String(128), nullable=False),
        sa.Column("model_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("capability_tags", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id",
            "provider_family",
            "model_key",
            name="uq_llm_model_definition_identity",
        ),
    )
    op.create_index(
        "ix_llm_model_definition_namespace_id",
        "llm_model_definition",
        ["namespace_id"],
    )
    op.add_column(
        "llm_provider_model",
        sa.Column("model_definition_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_llm_provider_model_model_definition_id",
        "llm_provider_model",
        ["model_definition_id"],
    )
    op.create_foreign_key(
        "fk_llm_provider_model_definition",
        "llm_provider_model",
        "llm_model_definition",
        ["model_definition_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _create_runtime_tables() -> None:
    op.create_table(
        "runtime_instance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_node_id", sa.Uuid(), nullable=True),
        sa.Column("legacy_runtime_profile_id", sa.Uuid(), nullable=True),
        sa.Column(
            "location_type",
            _enum("runtimelocationtype", "platform", "node"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("installation_key", sa.String(255), nullable=False),
        sa.Column(
            "engine_type",
            _enum("runtimeenginetype", "claude_code", "codex"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(64), nullable=True),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("executable_fingerprint", sa.String(128), nullable=True),
        sa.Column(
            "status",
            _enum(
                "runtimeinstancestatus",
                "discovered",
                "available",
                "unavailable",
                "incompatible",
                "disabled",
            ),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("desired_configuration_revision_id", sa.Uuid(), nullable=True),
        sa.Column("applied_configuration_revision_id", sa.Uuid(), nullable=True),
        sa.Column("current_capability_report_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["runtime_node_id"], ["runtime_node.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["legacy_runtime_profile_id"], ["runtime_profile.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_node_id",
            "installation_key",
            name="uq_runtime_instance_node_installation",
        ),
        sa.UniqueConstraint(
            "legacy_runtime_profile_id", name="uq_runtime_instance_legacy_profile"
        ),
    )
    op.create_index("ix_runtime_instance_namespace_id", "runtime_instance", ["namespace_id"])
    op.create_index("ix_runtime_instance_runtime_node_id", "runtime_instance", ["runtime_node_id"])
    op.create_index(
        "uq_runtime_instance_platform_installation",
        "runtime_instance",
        ["namespace_id", "installation_key"],
        unique=True,
        postgresql_where=sa.text("location_type = 'platform'"),
        sqlite_where=sa.text("location_type = 'platform'"),
    )
    op.create_table(
        "runtime_configuration_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("executable", sa.String(1024), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("working_directory_policy", sa.String(64), nullable=False),
        sa.Column("environment_allowlist", sa.JSON(), nullable=False),
        sa.Column("security_policy", sa.JSON(), nullable=False),
        sa.Column("resource_limits", sa.JSON(), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column(
            "status",
            _enum("runtimeconfigurationstatus", "desired", "applied", "failed"),
            nullable=False,
        ),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_instance_id", "revision", name="uq_runtime_configuration_revision"
        ),
    )
    op.create_index(
        "ix_runtime_configuration_revision_runtime_instance_id",
        "runtime_configuration_revision",
        ["runtime_instance_id"],
    )
    op.create_table(
        "runtime_capability_report",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=True),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("discovered_models", sa.JSON(), nullable=False),
        sa.Column("capability_fingerprint", sa.String(64), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_instance_id", "generation", name="uq_runtime_capability_generation"
        ),
    )
    op.create_index(
        "ix_runtime_capability_report_runtime_instance_id",
        "runtime_capability_report",
        ["runtime_instance_id"],
    )
    op.create_foreign_key(
        "fk_runtime_instance_desired_configuration",
        "runtime_instance",
        "runtime_configuration_revision",
        ["desired_configuration_revision_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_runtime_instance_applied_configuration",
        "runtime_instance",
        "runtime_configuration_revision",
        ["applied_configuration_revision_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_runtime_instance_current_capability",
        "runtime_instance",
        "runtime_capability_report",
        ["current_capability_report_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_table(
        "runtime_model_binding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("model_definition_id", sa.Uuid(), nullable=False),
        sa.Column("provider_config_id", sa.Uuid(), nullable=True),
        sa.Column("provider_model_id", sa.Uuid(), nullable=True),
        sa.Column(
            "route_type",
            _enum(
                "runtimemodelroutetype",
                "provider_config",
                "runtime_native",
                "legacy_direct",
            ),
            nullable=False,
        ),
        sa.Column("route_key", sa.String(255), nullable=False),
        sa.Column("engine_model_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            _enum(
                "runtimemodelbindingstatus",
                "declared",
                "available",
                "unmapped",
                "failed",
                "disabled",
            ),
            nullable=False,
        ),
        sa.Column("validation_fingerprint", sa.String(64), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["model_definition_id"], ["llm_model_definition.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_model_id"], ["llm_provider_model.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_instance_id",
            "model_definition_id",
            "route_type",
            "route_key",
            name="uq_runtime_model_binding_route",
        ),
    )
    op.create_index("ix_runtime_model_binding_namespace_id", "runtime_model_binding", ["namespace_id"])
    op.create_index(
        "ix_runtime_model_binding_runtime_instance_id",
        "runtime_model_binding",
        ["runtime_instance_id"],
    )
    op.create_table(
        "runtime_model_validation_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_model_binding_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["runtime_model_binding_id"],
            ["runtime_model_binding.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "runtime_model_binding_id",
            "attempt_no",
            name="uq_runtime_model_validation_attempt",
        ),
    )
    op.create_index(
        "ix_runtime_model_validation_attempt_runtime_model_binding_id",
        "runtime_model_validation_attempt",
        ["runtime_model_binding_id"],
    )


def _add_domain_columns() -> None:
    op.add_column("runtime_node", sa.Column("discovery_generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_session", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.create_index("ix_agent_session_runtime_instance_id", "agent_session", ["runtime_instance_id"])
    op.create_foreign_key("fk_agent_session_runtime_instance", "agent_session", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.alter_column("agent_session", "runtime_profile_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("agent_task", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.create_index("ix_agent_task_runtime_instance_id", "agent_task", ["runtime_instance_id"])
    op.create_foreign_key("fk_agent_task_runtime_instance", "agent_task", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.alter_column("agent_task", "runtime_profile_id", existing_type=sa.Uuid(), nullable=True)

    op.add_column("agent_draft", sa.Column("preferred_model_definition_id", sa.Uuid(), nullable=True))
    op.add_column("agent_draft", sa.Column("execution_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_agent_draft_preferred_model_definition_id", "agent_draft", ["preferred_model_definition_id"])
    op.create_foreign_key("fk_agent_draft_preferred_model_definition", "agent_draft", "llm_model_definition", ["preferred_model_definition_id"], ["id"], ondelete="RESTRICT")
    op.add_column("agent_release", sa.Column("preferred_model_definition_id", sa.Uuid(), nullable=True))
    op.add_column("agent_release", sa.Column("required_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_agent_release_preferred_model_definition_id", "agent_release", ["preferred_model_definition_id"])
    op.create_foreign_key("fk_agent_release_preferred_model_definition", "agent_release", "llm_model_definition", ["preferred_model_definition_id"], ["id"], ondelete="RESTRICT")

    op.create_table(
        "agent_release_runtime_compatibility",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_capability_report_id", sa.Uuid(), nullable=True),
        sa.Column("model_catalog_fingerprint", sa.String(64), nullable=True),
        sa.Column("deployable", sa.Boolean(), nullable=False),
        sa.Column("preference_status", sa.String(32), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["agent_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_capability_report_id"], ["runtime_capability_report.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("release_id", "runtime_instance_id", name="uq_agent_release_runtime_compatibility"),
    )
    op.create_table(
        "agent_activation_precheck",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_configuration_revision_id", sa.Uuid(), nullable=True),
        sa.Column("runtime_capability_report_id", sa.Uuid(), nullable=True),
        sa.Column("model_catalog_fingerprint", sa.String(64), nullable=False),
        sa.Column("preference_status", sa.String(32), nullable=False),
        sa.Column("deployable", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("precheck_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["agent_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_configuration_revision_id"], ["runtime_configuration_revision.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runtime_capability_report_id"], ["runtime_capability_report.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_activation_precheck_precheck_digest", "agent_activation_precheck", ["precheck_digest"])
    op.add_column("agent_activation", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.add_column("agent_activation", sa.Column("precheck_id", sa.Uuid(), nullable=True))
    op.create_index("ix_agent_activation_runtime_instance_id", "agent_activation", ["runtime_instance_id"])
    op.create_foreign_key("fk_agent_activation_runtime_instance", "agent_activation", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_agent_activation_precheck", "agent_activation", "agent_activation_precheck", ["precheck_id"], ["id"], ondelete="RESTRICT")
    op.add_column("agent_deployment", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.create_index("ix_agent_deployment_runtime_instance_id", "agent_deployment", ["runtime_instance_id"])
    op.create_foreign_key("fk_agent_deployment_runtime_instance", "agent_deployment", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.alter_column("agent_deployment", "runtime_profile_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("runtime_agent_release", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.add_column("runtime_agent_release", sa.Column("runtime_configuration_revision_id", sa.Uuid(), nullable=True))
    op.add_column("runtime_agent_release", sa.Column("runtime_capability_report_id", sa.Uuid(), nullable=True))
    op.add_column("runtime_agent_release", sa.Column("adapter_version", sa.String(64), nullable=True))
    op.add_column("runtime_agent_release", sa.Column("runtime_model_catalog_fingerprint", sa.String(64), nullable=True))
    op.add_column("runtime_agent_release", sa.Column("effective_spec_digest", sa.String(64), nullable=True))
    op.create_index("ix_runtime_agent_release_runtime_instance_id", "runtime_agent_release", ["runtime_instance_id"])
    op.create_index("uq_runtime_agent_release_v09", "runtime_agent_release", ["runtime_instance_id", "agent_id"], unique=True, postgresql_where=sa.text("runtime_instance_id IS NOT NULL"), sqlite_where=sa.text("runtime_instance_id IS NOT NULL"))
    op.create_foreign_key("fk_runtime_agent_release_runtime_instance", "runtime_agent_release", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_runtime_agent_release_configuration", "runtime_agent_release", "runtime_configuration_revision", ["runtime_configuration_revision_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_runtime_agent_release_capability", "runtime_agent_release", "runtime_capability_report", ["runtime_capability_report_id"], ["id"], ondelete="RESTRICT")
    op.alter_column("runtime_agent_release", "runtime_profile_id", existing_type=sa.Uuid(), nullable=True)

    op.add_column("conversation", sa.Column("runtime_instance_id", sa.Uuid(), nullable=True))
    op.create_index("ix_conversation_runtime_instance_id", "conversation", ["runtime_instance_id"])
    op.create_foreign_key("fk_conversation_runtime_instance", "conversation", "runtime_instance", ["runtime_instance_id"], ["id"], ondelete="RESTRICT")
    op.add_column("conversation_configuration_revision", sa.Column("runtime_model_catalog_fingerprint", sa.String(64), nullable=True))
    op.create_table(
        "conversation_execution_binding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("configuration_revision_id", sa.Uuid(), nullable=False),
        sa.Column("role_key", sa.String(128), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_agent_release_id", sa.Uuid(), nullable=True),
        sa.Column("agent_release_id", sa.Uuid(), nullable=True),
        sa.Column("model_selection_mode", sa.String(32), nullable=False),
        sa.Column("preferred_model_definition_id", sa.Uuid(), nullable=True),
        sa.Column("runtime_model_binding_id", sa.Uuid(), nullable=False),
        sa.Column("selection_source", sa.String(32), nullable=False),
        sa.Column("runtime_configuration_revision_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_capability_report_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("model_catalog_fingerprint", sa.String(64), nullable=False),
        sa.Column("effective_spec_digest", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["configuration_revision_id"], ["conversation_configuration_revision.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instance.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_agent_release_id"], ["runtime_agent_release.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_release_id"], ["agent_release.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preferred_model_definition_id"], ["llm_model_definition.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_model_binding_id"], ["runtime_model_binding.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_configuration_revision_id"], ["runtime_configuration_revision.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_capability_report_id"], ["runtime_capability_report.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("configuration_revision_id", "role_key", name="uq_conversation_execution_binding_role"),
    )

    workflow_columns = [
        ("runtime_instance_id", sa.Uuid()),
        ("runtime_agent_release_id", sa.Uuid()),
        ("model_selection_mode", sa.String(32)),
        ("preferred_model_definition_id", sa.Uuid()),
        ("runtime_model_binding_id", sa.Uuid()),
        ("selection_source", sa.String(32)),
        ("runtime_configuration_revision_id", sa.Uuid()),
        ("runtime_capability_report_id", sa.Uuid()),
        ("runtime_model_catalog_fingerprint", sa.String(64)),
        ("adapter_version", sa.String(64)),
        ("effective_spec_digest", sa.String(64)),
    ]
    for name, type_ in workflow_columns:
        op.add_column("workflow_execution_node_binding", sa.Column(name, type_, nullable=True))
    op.alter_column("workflow_execution_node_binding", "runtime_profile_id", existing_type=sa.Uuid(), nullable=True)
    fk_targets = {
        "runtime_instance_id": ("runtime_instance", "id"),
        "runtime_agent_release_id": ("runtime_agent_release", "id"),
        "preferred_model_definition_id": ("llm_model_definition", "id"),
        "runtime_model_binding_id": ("runtime_model_binding", "id"),
        "runtime_configuration_revision_id": ("runtime_configuration_revision", "id"),
        "runtime_capability_report_id": ("runtime_capability_report", "id"),
    }
    for column, (target, target_column) in fk_targets.items():
        op.create_foreign_key(f"fk_workflow_execution_binding_{column}", "workflow_execution_node_binding", target, [column], [target_column], ondelete="RESTRICT")
    op.add_column("project", sa.Column("default_runtime_instance_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_project_default_runtime_instance", "project", "runtime_instance", ["default_runtime_instance_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "agent_task_model_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_node_id", sa.Uuid(), nullable=True),
        sa.Column("agent_release_id", sa.Uuid(), nullable=True),
        sa.Column("model_definition_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_model_binding_id", sa.Uuid(), nullable=False),
        sa.Column("engine_type", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=True),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("route_type", sa.String(32), nullable=False),
        sa.Column("route_reference", sa.String(255), nullable=True),
        sa.Column("model_selection_mode", sa.String(32), nullable=False),
        sa.Column("selection_source", sa.String(32), nullable=False),
        sa.Column("runtime_configuration_digest", sa.String(64), nullable=False),
        sa.Column("capability_fingerprint", sa.String(64), nullable=False),
        sa.Column("model_catalog_fingerprint", sa.String(64), nullable=False),
        sa.Column("effective_spec_digest", sa.String(64), nullable=False),
        sa.Column("runtime_evidence", sa.JSON(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["agent_task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instance.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_node_id"], ["runtime_node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_release_id"], ["agent_release.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_definition_id"], ["llm_model_definition.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_model_binding_id"], ["runtime_model_binding.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_agent_task_model_usage"),
    )


def _backfill() -> None:
    connection = op.get_bind()
    now = sa.func.now()
    provider_models = connection.execute(
        sa.text(
            "SELECT m.id, m.model_id, m.display_name, m.source_type, m.sync_status, "
            "c.namespace_id, c.provider_slug "
            "FROM llm_provider_model m JOIN llm_provider_config c ON c.id = m.provider_config_id"
        )
    ).all()
    ambiguous_models = [
        row
        for row in provider_models
        if row.source_type != "discovered"
        or row.sync_status != "active"
        or str(row.provider_slug).lower() == "custom"
    ]
    if ambiguous_models:
        identifiers = ", ".join(str(row.id) for row in ambiguous_models[:20])
        raise RuntimeError(
            "v0.9 model identity migration requires Admin confirmation for "
            f"manual, stale, or custom models: {identifiers}"
        )
    definitions: dict[tuple[object, str, str], uuid.UUID] = {}
    for row in provider_models:
        key = (row.namespace_id, str(row.provider_slug).lower(), row.model_id)
        definition_id = definitions.get(key)
        if definition_id is None:
            definition_id = uuid.uuid4()
            definitions[key] = definition_id
            connection.execute(
                sa.text(
                    "INSERT INTO llm_model_definition "
                    "(id, namespace_id, provider_family, model_key, display_name, capability_tags, enabled, created_at, updated_at) "
                    "VALUES (:id, :namespace_id, :provider_family, :model_key, :display_name, :tags, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": definition_id,
                    "namespace_id": row.namespace_id,
                    "provider_family": str(row.provider_slug).lower(),
                    "model_key": row.model_id,
                    "display_name": row.display_name,
                    "tags": json.dumps([]),
                },
            )
        connection.execute(
            sa.text("UPDATE llm_provider_model SET model_definition_id = :definition_id WHERE id = :model_id"),
            {"definition_id": definition_id, "model_id": row.id},
        )

    runtime_rows = connection.execute(
        sa.text(
            "SELECT r.id, r.namespace_id, r.runtime_type, r.route_mode, r.provider_config_id, "
            "r.model_id, r.permission_mode, r.config, r.harness_capabilities, r.created_at, r.updated_at, "
            "n.id AS node_id, n.name AS node_name, n.last_seen_at "
            "FROM runtime_profile r LEFT JOIN runtime_node n ON n.runtime_profile_id = r.id"
        )
    ).all()
    runtime_map: dict[object, uuid.UUID] = {}
    runtime_evidence: dict[object, tuple[uuid.UUID, uuid.UUID]] = {}
    for row in runtime_rows:
        if row.runtime_type == "node" and row.node_id is None:
            raise RuntimeError(f"Runtime Profile {row.id} is node-scoped but has no Runtime Node")
        runtime_id = uuid.uuid4()
        config_id = uuid.uuid4()
        report_id = uuid.uuid4()
        runtime_map[row.id] = runtime_id
        config_payload = {
            "executable": "claude",
            "arguments": [],
            "working_directory_policy": "workspace",
            "environment_allowlist": [],
            "security_policy": {"permission_mode": row.permission_mode},
            "resource_limits": row.config or {},
        }
        config_digest = _digest(config_payload)
        capabilities = row.harness_capabilities or {}
        capability_digest = _digest(
            {
                "engine_type": "claude_code",
                "engine_version": None,
                "adapter_version": "1.0.0",
                "configuration_digest": config_digest,
                "capabilities": capabilities,
            }
        )
        connection.execute(
            sa.text(
                "INSERT INTO runtime_instance "
                "(id, namespace_id, runtime_node_id, legacy_runtime_profile_id, location_type, name, installation_key, engine_type, engine_version, adapter_version, executable_fingerprint, status, enabled, desired_configuration_revision_id, applied_configuration_revision_id, current_capability_report_id, last_seen_at, created_at, updated_at) "
                "VALUES (:id, :namespace_id, :node_id, :legacy_id, :location, :name, :installation_key, 'claude_code', NULL, '1.0.0', NULL, :status, :enabled, NULL, NULL, NULL, :last_seen_at, :created_at, :updated_at)"
            ),
            {
                "id": runtime_id,
                "namespace_id": row.namespace_id,
                "node_id": row.node_id,
                "legacy_id": row.id,
                "location": row.runtime_type,
                "name": row.node_name or "Platform Claude Code",
                "installation_key": f"legacy-runtime-profile:{row.id}",
                "status": "discovered",
                "enabled": False,
                "last_seen_at": row.last_seen_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO runtime_configuration_revision "
                "(id, runtime_instance_id, revision, executable, arguments, working_directory_policy, environment_allowlist, security_policy, resource_limits, configuration_digest, status, error, created_by, created_at, applied_at) "
                "VALUES (:id, :runtime_id, 1, 'claude', :arguments, 'workspace', :environment, :security, :limits, :digest, 'desired', NULL, NULL, CURRENT_TIMESTAMP, NULL)"
            ),
            {
                "id": config_id,
                "runtime_id": runtime_id,
                "arguments": json.dumps([]),
                "environment": json.dumps([]),
                "security": json.dumps(config_payload["security_policy"]),
                "limits": json.dumps(config_payload["resource_limits"]),
                "digest": config_digest,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO runtime_capability_report "
                "(id, runtime_instance_id, generation, engine_version, adapter_version, configuration_digest, capabilities, discovered_models, capability_fingerprint, reported_at) "
                "VALUES (:id, :runtime_id, 1, NULL, '1.0.0', :config_digest, :capabilities, :models, :fingerprint, CURRENT_TIMESTAMP)"
            ),
            {
                "id": report_id,
                "runtime_id": runtime_id,
                "config_digest": config_digest,
                "capabilities": json.dumps(capabilities),
                "models": json.dumps([]),
                "fingerprint": capability_digest,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE runtime_instance SET desired_configuration_revision_id = :config_id, applied_configuration_revision_id = NULL, current_capability_report_id = NULL WHERE id = :runtime_id"
            ),
            {"config_id": config_id, "report_id": report_id, "runtime_id": runtime_id},
        )
        runtime_evidence[row.id] = (config_id, report_id)

        provider_family = "anthropic"
        provider_model_id = None
        model_definition_id = None
        if row.provider_config_id:
            match = connection.execute(
                sa.text(
                    "SELECT m.id, m.model_definition_id, c.provider_slug FROM llm_provider_model m "
                    "JOIN llm_provider_config c ON c.id = m.provider_config_id "
                    "WHERE m.provider_config_id = :provider_id AND m.model_id = :model_id"
                ),
                {"provider_id": row.provider_config_id, "model_id": row.model_id},
            ).one_or_none()
            if match is None:
                raise RuntimeError(
                    f"Runtime Profile {row.id} provider route has no exact Provider Model"
                )
            provider_model_id = match.id
            model_definition_id = match.model_definition_id
            provider_family = match.provider_slug
        else:
            key = (row.namespace_id, provider_family, row.model_id)
            model_definition_id = definitions.get(key)
            if model_definition_id is None:
                model_definition_id = uuid.uuid4()
                definitions[key] = model_definition_id
                connection.execute(
                    sa.text(
                        "INSERT INTO llm_model_definition (id, namespace_id, provider_family, model_key, display_name, capability_tags, enabled, created_at, updated_at) "
                        "VALUES (:id, :namespace_id, :family, :model_key, :display_name, :tags, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": model_definition_id,
                        "namespace_id": row.namespace_id,
                        "family": provider_family,
                        "model_key": row.model_id,
                        "display_name": row.model_id,
                        "tags": json.dumps([]),
                    },
                )
        route_type = "provider_config" if row.provider_config_id else "legacy_direct"
        binding_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO runtime_model_binding "
                "(id, namespace_id, runtime_instance_id, model_definition_id, provider_config_id, provider_model_id, route_type, route_key, engine_model_id, status, validation_fingerprint, last_validated_at, last_error, created_at, updated_at) "
                "VALUES (:id, :namespace_id, :runtime_id, :definition_id, :provider_id, :provider_model_id, :route_type, :route_key, :engine_model_id, :status, :fingerprint, :validated_at, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": binding_id,
                "namespace_id": row.namespace_id,
                "runtime_id": runtime_id,
                "definition_id": model_definition_id,
                "provider_id": row.provider_config_id,
                "provider_model_id": provider_model_id,
                "route_type": route_type,
                "route_key": str(row.provider_config_id or row.id),
                "engine_model_id": row.model_id,
                "status": "declared",
                "fingerprint": None,
                "validated_at": None,
            },
        )

    for old_id, new_id in runtime_map.items():
        for table, old_column, new_column in [
            ("agent_session", "runtime_profile_id", "runtime_instance_id"),
            ("agent_task", "runtime_profile_id", "runtime_instance_id"),
            ("agent_deployment", "runtime_profile_id", "runtime_instance_id"),
            ("runtime_agent_release", "runtime_profile_id", "runtime_instance_id"),
            ("conversation", "runtime_id", "runtime_instance_id"),
            ("workflow_execution_node_binding", "runtime_profile_id", "runtime_instance_id"),
            ("project", "default_runtime_id", "default_runtime_instance_id"),
        ]:
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET {new_column} = :new_id WHERE {old_column} = :old_id"
                ),
                {"new_id": new_id, "old_id": old_id},
            )
        config_id, report_id = runtime_evidence[old_id]
        connection.execute(
            sa.text(
                "UPDATE runtime_agent_release SET runtime_configuration_revision_id = :config_id, runtime_capability_report_id = :report_id, adapter_version = '1.0.0' WHERE runtime_profile_id = :old_id"
            ),
            {"config_id": config_id, "report_id": report_id, "old_id": old_id},
        )

    draft_rows = connection.execute(
        sa.text(
            "SELECT d.id, d.provider_config_id, d.model_id FROM agent_draft d WHERE d.provider_config_id IS NOT NULL AND d.model_id IS NOT NULL"
        )
    ).all()
    for row in draft_rows:
        definition_id = connection.execute(
            sa.text(
                "SELECT model_definition_id FROM llm_provider_model WHERE provider_config_id = :provider_id AND model_id = :model_id"
            ),
            {"provider_id": row.provider_config_id, "model_id": row.model_id},
        ).scalar_one_or_none()
        if definition_id is None:
            raise RuntimeError(f"Agent Draft {row.id} model cannot be mapped exactly")
        connection.execute(
            sa.text("UPDATE agent_draft SET preferred_model_definition_id = :definition_id WHERE id = :id"),
            {"definition_id": definition_id, "id": row.id},
        )


def upgrade() -> None:
    _create_model_tables()
    _create_runtime_tables()
    _add_domain_columns()
    _backfill()


def downgrade() -> None:
    op.drop_table("agent_task_model_usage")
    op.drop_constraint("fk_project_default_runtime_instance", "project", type_="foreignkey")
    op.drop_column("project", "default_runtime_instance_id")
    for column in [
        "runtime_instance_id",
        "runtime_agent_release_id",
        "preferred_model_definition_id",
        "runtime_model_binding_id",
        "runtime_configuration_revision_id",
        "runtime_capability_report_id",
    ]:
        op.drop_constraint(f"fk_workflow_execution_binding_{column}", "workflow_execution_node_binding", type_="foreignkey")
    for column in [
        "effective_spec_digest",
        "adapter_version",
        "runtime_model_catalog_fingerprint",
        "runtime_capability_report_id",
        "runtime_configuration_revision_id",
        "selection_source",
        "runtime_model_binding_id",
        "preferred_model_definition_id",
        "model_selection_mode",
        "runtime_agent_release_id",
        "runtime_instance_id",
    ]:
        op.drop_column("workflow_execution_node_binding", column)
    op.alter_column("workflow_execution_node_binding", "runtime_profile_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("conversation_execution_binding")
    op.drop_column("conversation_configuration_revision", "runtime_model_catalog_fingerprint")
    op.drop_constraint("fk_conversation_runtime_instance", "conversation", type_="foreignkey")
    op.drop_index("ix_conversation_runtime_instance_id", table_name="conversation")
    op.drop_column("conversation", "runtime_instance_id")
    for name in [
        "effective_spec_digest",
        "runtime_model_catalog_fingerprint",
        "adapter_version",
        "runtime_capability_report_id",
        "runtime_configuration_revision_id",
    ]:
        op.drop_column("runtime_agent_release", name)
    op.drop_constraint("fk_runtime_agent_release_runtime_instance", "runtime_agent_release", type_="foreignkey")
    op.drop_index("uq_runtime_agent_release_v09", table_name="runtime_agent_release")
    op.drop_index("ix_runtime_agent_release_runtime_instance_id", table_name="runtime_agent_release")
    op.drop_column("runtime_agent_release", "runtime_instance_id")
    op.alter_column("runtime_agent_release", "runtime_profile_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint("fk_agent_deployment_runtime_instance", "agent_deployment", type_="foreignkey")
    op.drop_index("ix_agent_deployment_runtime_instance_id", table_name="agent_deployment")
    op.drop_column("agent_deployment", "runtime_instance_id")
    op.alter_column("agent_deployment", "runtime_profile_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint("fk_agent_activation_precheck", "agent_activation", type_="foreignkey")
    op.drop_constraint("fk_agent_activation_runtime_instance", "agent_activation", type_="foreignkey")
    op.drop_index("ix_agent_activation_runtime_instance_id", table_name="agent_activation")
    op.drop_column("agent_activation", "precheck_id")
    op.drop_column("agent_activation", "runtime_instance_id")
    op.drop_table("agent_activation_precheck")
    op.drop_table("agent_release_runtime_compatibility")
    op.drop_constraint("fk_agent_release_preferred_model_definition", "agent_release", type_="foreignkey")
    op.drop_index("ix_agent_release_preferred_model_definition_id", table_name="agent_release")
    op.drop_column("agent_release", "required_capabilities")
    op.drop_column("agent_release", "preferred_model_definition_id")
    op.drop_constraint("fk_agent_draft_preferred_model_definition", "agent_draft", type_="foreignkey")
    op.drop_index("ix_agent_draft_preferred_model_definition_id", table_name="agent_draft")
    op.drop_column("agent_draft", "execution_policy")
    op.drop_column("agent_draft", "preferred_model_definition_id")
    for table in ["agent_task", "agent_session"]:
        op.drop_constraint(f"fk_{table}_runtime_instance", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_runtime_instance_id", table_name=table)
        op.drop_column(table, "runtime_instance_id")
        op.alter_column(table, "runtime_profile_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("runtime_node", "discovery_generation")
    op.drop_table("runtime_model_validation_attempt")
    op.drop_table("runtime_model_binding")
    op.drop_constraint("fk_runtime_instance_current_capability", "runtime_instance", type_="foreignkey")
    op.drop_constraint("fk_runtime_instance_applied_configuration", "runtime_instance", type_="foreignkey")
    op.drop_constraint("fk_runtime_instance_desired_configuration", "runtime_instance", type_="foreignkey")
    op.drop_table("runtime_capability_report")
    op.drop_table("runtime_configuration_revision")
    op.drop_table("runtime_instance")
    op.drop_constraint("fk_llm_provider_model_definition", "llm_provider_model", type_="foreignkey")
    op.drop_index("ix_llm_provider_model_model_definition_id", table_name="llm_provider_model")
    op.drop_column("llm_provider_model", "model_definition_id")
    op.drop_table("llm_model_definition")

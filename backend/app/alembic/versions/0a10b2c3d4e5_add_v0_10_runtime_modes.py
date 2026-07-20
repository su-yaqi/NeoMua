"""add v0.10 runtime management modes

Revision ID: 0a10b2c3d4e5
Revises: fc5a7b9d1e34
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0a10b2c3d4e5"
down_revision: str | None = "fc5a7b9d1e34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


runtime_management_type = postgresql.ENUM(
    "platform_builtin",
    "service_managed",
    "client_discovered",
    "legacy_manual",
    name="runtimemanagementtype",
    create_type=False,
)
runtime_node_mode = postgresql.ENUM(
    "service",
    "client",
    "legacy_unclassified",
    name="runtimenodemode",
    create_type=False,
)
runtime_configuration_origin = postgresql.ENUM(
    "system_builtin",
    "service_manifest",
    "client_adapter",
    "legacy_manual",
    name="runtimeconfigurationorigin",
    create_type=False,
)
runtime_binding_origin = postgresql.ENUM(
    "llm_config_reconciled",
    "runtime_native_discovered",
    "legacy_manual",
    name="runtimemodelbindingorigin",
    create_type=False,
)
reconcile_status = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", name="reconcilestatus", create_type=False
)
bootstrap_status = postgresql.ENUM(
    "waiting_for_install",
    "preflighted",
    "staged",
    "enrolled",
    "service_activated",
    "reconciled",
    "failed",
    "revoked",
    name="bootstrapstatus",
    create_type=False,
)
runtime_control_action = postgresql.ENUM(
    "auto_enable", "pause", "resume", name="runtimecontrolaction", create_type=False
)
runtime_json = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE runtimeenginetype ADD VALUE IF NOT EXISTS 'claude_agent_sdk'"
        )
        runtime_management_type.create(bind, checkfirst=True)
        runtime_node_mode.create(bind, checkfirst=True)
        runtime_configuration_origin.create(bind, checkfirst=True)
        runtime_binding_origin.create(bind, checkfirst=True)
        reconcile_status.create(bind, checkfirst=True)
        bootstrap_status.create(bind, checkfirst=True)
        runtime_control_action.create(bind, checkfirst=True)

    op.add_column(
        "runtime_node",
        sa.Column(
            "management_mode",
            runtime_node_mode if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
            server_default="legacy_unclassified",
        ),
    )
    op.add_column(
        "runtime_node",
        sa.Column("adapter_registry_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "runtime_node",
        sa.Column(
            "discovery_requested_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "runtime_instance",
        sa.Column(
            "management_type",
            runtime_management_type
            if bind.dialect.name == "postgresql"
            else sa.String(32),
            nullable=False,
            server_default="legacy_manual",
        ),
    )
    op.add_column(
        "runtime_instance",
        sa.Column("lifecycle_source_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "uq_runtime_instance_platform_builtin",
        "runtime_instance",
        ["namespace_id"],
        unique=True,
        postgresql_where=sa.text("management_type = 'platform_builtin'"),
        sqlite_where=sa.text("management_type = 'platform_builtin'"),
    )
    op.add_column(
        "runtime_configuration_revision",
        sa.Column(
            "origin",
            runtime_configuration_origin
            if bind.dialect.name == "postgresql"
            else sa.String(32),
            nullable=False,
            server_default="legacy_manual",
        ),
    )
    op.add_column(
        "runtime_configuration_revision",
        sa.Column("adapter_execution_ref", sa.String(255), nullable=True),
    )
    op.add_column(
        "runtime_model_binding",
        sa.Column(
            "origin",
            runtime_binding_origin
            if bind.dialect.name == "postgresql"
            else sa.String(32),
            nullable=False,
            server_default="legacy_manual",
        ),
    )
    op.add_column(
        "node_enrollment_token",
        sa.Column(
            "requested_management_mode",
            runtime_node_mode if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
            server_default="legacy_unclassified",
        ),
    )
    op.add_column(
        "node_enrollment_token",
        sa.Column("preflight_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "node_enrollment_token",
        sa.Column("bound_public_key_fingerprint", sa.String(128), nullable=True),
    )
    op.add_column(
        "node_enrollment_token",
        sa.Column("distribution_manifest_digest", sa.String(64), nullable=True),
    )
    op.create_table(
        "platform_runtime_reconcile_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            reconcile_status if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", runtime_json, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id", "input_fingerprint", name="uq_platform_runtime_reconcile_input"
        ),
    )
    op.create_index(
        "ix_platform_runtime_reconcile_job_namespace_id",
        "platform_runtime_reconcile_job",
        ["namespace_id"],
    )
    op.create_table(
        "platform_runtime_reconcile_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            reconcile_status if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("result", runtime_json, nullable=True),
        sa.Column("error", runtime_json, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["platform_runtime_reconcile_job.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_platform_reconcile_attempt"),
    )
    op.create_index(
        "ix_platform_runtime_reconcile_attempt_job_id",
        "platform_runtime_reconcile_attempt",
        ["job_id"],
    )
    op.create_table(
        "llm_provider_model_validation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("provider_config_id", sa.Uuid(), nullable=False),
        sa.Column("provider_model_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        sa.Column("evidence", runtime_json, nullable=True),
        sa.Column("error", runtime_json, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["provider_model_id"], ["llm_provider_model.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_model_id",
            "configuration_fingerprint",
            "attempt_no",
            name="uq_llm_provider_model_validation_attempt",
        ),
    )
    op.create_index(
        "ix_llm_provider_model_validation_namespace_id",
        "llm_provider_model_validation",
        ["namespace_id"],
    )
    op.create_index(
        "ix_llm_provider_model_validation_provider_model_id",
        "llm_provider_model_validation",
        ["provider_model_id"],
    )
    op.add_column(
        "runtime_model_binding",
        sa.Column("provider_model_validation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_runtime_model_binding_provider_validation",
        "runtime_model_binding",
        "llm_provider_model_validation",
        ["provider_model_validation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "node_distribution_release",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("release_key", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column(
            "management_mode",
            runtime_node_mode if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("os_name", sa.String(128), nullable=False),
        sa.Column("architecture", sa.String(64), nullable=False),
        sa.Column("manifest", runtime_json, nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signing_public_key", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("release_key", name="uq_node_distribution_release_key"),
    )
    op.create_table(
        "runtime_adapter_release",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("adapter_id", sa.String(128), nullable=False),
        sa.Column(
            "engine_type",
            sa.Enum(
                "claude_agent_sdk", "claude_code", "codex", name="runtimeenginetype"
            )
            if bind.dialect.name != "postgresql"
            else postgresql.ENUM(name="runtimeenginetype", create_type=False),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("discovery_contract", runtime_json, nullable=False),
        sa.Column("execution_contract", runtime_json, nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signing_public_key", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adapter_id", "version", name="uq_runtime_adapter_release"),
    )
    op.create_table(
        "node_bootstrap_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("enrollment_token_id", sa.Uuid(), nullable=False),
        sa.Column(
            "management_mode",
            runtime_node_mode if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("release_channel", sa.String(64), nullable=False),
        sa.Column("distribution_release_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            bootstrap_status if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("bound_public_key_fingerprint", sa.String(128), nullable=True),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("enrollment_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("enrollment_request_digest", sa.String(length=64), nullable=True),
        sa.Column("enrollment_response_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["enrollment_token_id"], ["node_enrollment_token.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["distribution_release_id"], ["node_distribution_release.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enrollment_token_id"),
    )
    op.create_index(
        "ix_node_bootstrap_session_namespace_id",
        "node_bootstrap_session",
        ["namespace_id"],
    )
    op.create_table(
        "node_bootstrap_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bootstrap_session_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "stage",
            bootstrap_status if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
        ),
        sa.Column("host_facts", runtime_json, nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=True),
        sa.Column("error", runtime_json, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bootstrap_session_id"], ["node_bootstrap_session.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bootstrap_session_id", "attempt_no", name="uq_bootstrap_attempt"
        ),
    )
    op.create_index(
        "ix_node_bootstrap_attempt_bootstrap_session_id",
        "node_bootstrap_attempt",
        ["bootstrap_session_id"],
    )
    op.create_table(
        "node_installation_receipt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("bootstrap_session_id", sa.Uuid(), nullable=True),
        sa.Column("distribution_release_id", sa.Uuid(), nullable=True),
        sa.Column("adapter_release_id", sa.Uuid(), nullable=True),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("components", runtime_json, nullable=False),
        sa.Column("logical_installation_ref", sa.String(255), nullable=False),
        sa.Column("device_signature", sa.Text(), nullable=False),
        sa.Column("first_applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["bootstrap_session_id"], ["node_bootstrap_session.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["distribution_release_id"], ["node_distribution_release.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["adapter_release_id"], ["runtime_adapter_release.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "receipt_digest", name="uq_node_installation_receipt"),
    )
    op.create_index(
        "ix_node_installation_receipt_node_id", "node_installation_receipt", ["node_id"]
    )
    op.add_column(
        "runtime_node", sa.Column("current_installation_receipt_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_runtime_node_current_installation_receipt",
        "runtime_node",
        "node_installation_receipt",
        ["current_installation_receipt_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_table(
        "runtime_discovery_observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=True),
        sa.Column("adapter_release_id", sa.Uuid(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("installation_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence", runtime_json, nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["runtime_instance_id"], ["runtime_instance.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["adapter_release_id"], ["runtime_adapter_release.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "node_id", "generation", "installation_key", name="uq_discovery_observation"
        ),
    )
    op.create_index(
        "ix_runtime_discovery_observation_node_id",
        "runtime_discovery_observation",
        ["node_id"],
    )
    op.add_column(
        "runtime_instance",
        sa.Column("current_discovery_observation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_runtime_instance_current_discovery_observation",
        "runtime_instance",
        "runtime_discovery_observation",
        ["current_discovery_observation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "runtime_capability_report",
        sa.Column("adapter_release_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_runtime_capability_adapter_release",
        "runtime_capability_report",
        "runtime_adapter_release",
        ["adapter_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "runtime_control_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action",
            runtime_control_action
            if bind.dialect.name == "postgresql"
            else sa.String(32),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(1024), nullable=True),
        sa.Column("evidence", runtime_json, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_control_decision_runtime_instance_id",
        "runtime_control_decision",
        ["runtime_instance_id"],
    )
    op.create_table(
        "runtime_installation_migration_receipt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("legacy_runtime_instance_id", sa.Uuid(), nullable=False),
        sa.Column("installation_key", sa.String(255), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("device_signature", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["runtime_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["legacy_runtime_instance_id"], ["runtime_instance.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_installation_migration_receipt_node_id",
        "runtime_installation_migration_receipt",
        ["node_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_runtime_capability_adapter_release",
        "runtime_capability_report",
        type_="foreignkey",
    )
    op.drop_column("runtime_capability_report", "adapter_release_id")
    op.drop_constraint(
        "fk_runtime_instance_current_discovery_observation",
        "runtime_instance",
        type_="foreignkey",
    )
    op.drop_column("runtime_instance", "current_discovery_observation_id")
    op.drop_index(
        "ix_runtime_installation_migration_receipt_node_id",
        table_name="runtime_installation_migration_receipt",
    )
    op.drop_table("runtime_installation_migration_receipt")
    op.drop_index(
        "ix_runtime_control_decision_runtime_instance_id",
        table_name="runtime_control_decision",
    )
    op.drop_table("runtime_control_decision")
    op.drop_index(
        "ix_runtime_discovery_observation_node_id",
        table_name="runtime_discovery_observation",
    )
    op.drop_table("runtime_discovery_observation")
    op.drop_constraint(
        "fk_runtime_node_current_installation_receipt",
        "runtime_node",
        type_="foreignkey",
    )
    op.drop_column("runtime_node", "current_installation_receipt_id")
    op.drop_index("ix_node_installation_receipt_node_id", table_name="node_installation_receipt")
    op.drop_table("node_installation_receipt")
    op.drop_index(
        "ix_node_bootstrap_attempt_bootstrap_session_id", table_name="node_bootstrap_attempt"
    )
    op.drop_table("node_bootstrap_attempt")
    op.drop_index("ix_node_bootstrap_session_namespace_id", table_name="node_bootstrap_session")
    op.drop_table("node_bootstrap_session")
    op.drop_table("runtime_adapter_release")
    op.drop_table("node_distribution_release")
    op.drop_constraint(
        "fk_runtime_model_binding_provider_validation",
        "runtime_model_binding",
        type_="foreignkey",
    )
    op.drop_column("runtime_model_binding", "provider_model_validation_id")
    op.drop_index(
        "ix_llm_provider_model_validation_provider_model_id",
        table_name="llm_provider_model_validation",
    )
    op.drop_index(
        "ix_llm_provider_model_validation_namespace_id",
        table_name="llm_provider_model_validation",
    )
    op.drop_table("llm_provider_model_validation")
    op.drop_index(
        "ix_platform_runtime_reconcile_attempt_job_id",
        table_name="platform_runtime_reconcile_attempt",
    )
    op.drop_table("platform_runtime_reconcile_attempt")
    op.drop_index(
        "ix_platform_runtime_reconcile_job_namespace_id",
        table_name="platform_runtime_reconcile_job",
    )
    op.drop_table("platform_runtime_reconcile_job")
    op.drop_column("node_enrollment_token", "distribution_manifest_digest")
    op.drop_column("node_enrollment_token", "bound_public_key_fingerprint")
    op.drop_column("node_enrollment_token", "preflight_at")
    op.drop_column("node_enrollment_token", "requested_management_mode")
    op.drop_column("runtime_node", "discovery_requested_generation")
    op.drop_column("runtime_model_binding", "origin")
    op.drop_column("runtime_configuration_revision", "adapter_execution_ref")
    op.drop_column("runtime_configuration_revision", "origin")
    op.drop_index("uq_runtime_instance_platform_builtin", table_name="runtime_instance")
    op.drop_column("runtime_instance", "lifecycle_source_key")
    op.drop_column("runtime_instance", "management_type")
    op.drop_column("runtime_node", "adapter_registry_digest")
    op.drop_column("runtime_node", "management_mode")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        runtime_binding_origin.drop(bind, checkfirst=True)
        runtime_configuration_origin.drop(bind, checkfirst=True)
        runtime_node_mode.drop(bind, checkfirst=True)
        runtime_management_type.drop(bind, checkfirst=True)
        runtime_control_action.drop(bind, checkfirst=True)
        bootstrap_status.drop(bind, checkfirst=True)
        reconcile_status.drop(bind, checkfirst=True)

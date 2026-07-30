"""add v0.6 Runtime jobs

Revision ID: c9d7e6f5a401
Revises: b5876b129fbd
Create Date: 2026-07-13 16:30:00
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d7e6f5a401"
down_revision = "b5876b129fbd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    runtime_job_kind = postgresql.ENUM(
        "workflow_handler",
        "workflow_validator",
        "repository_probe",
        name="runtimejobkind",
        create_type=False,
    )
    runtime_job_status = postgresql.ENUM(
        "queued",
        "dispatched",
        "running",
        "succeeded",
        "failed",
        "needs_manual_resolution",
        name="runtimejobstatus",
        create_type=False,
    )
    runtime_job_kind.create(op.get_bind(), checkfirst=True)
    runtime_job_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "runtime_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_profile_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=True),
        sa.Column("kind", runtime_job_kind, nullable=False),
        sa.Column("status", runtime_job_status, nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "result",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "error",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("side_effecting", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sqlmodel.sql.sqltypes.AutoString(length=255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_connection_id", sa.Uuid()),
        sa.Column("dispatch_reserved_until", sa.DateTime(timezone=True)),
        sa.Column(
            "idempotency_key",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"], ["runtime_profile.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"], ["runtime_node.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_runtime_job_idempotency"
        ),
    )
    op.create_index("ix_runtime_job_namespace_id", "runtime_job", ["namespace_id"])
    op.create_index("ix_runtime_job_target_node_id", "runtime_job", ["target_node_id"])
    op.create_index(
        "ix_runtime_job_dispatch_connection_id",
        "runtime_job",
        ["dispatch_connection_id"],
    )
    op.create_index(
        "ix_runtime_job_dispatch_reserved_until",
        "runtime_job",
        ["dispatch_reserved_until"],
    )
    op.create_index(
        "ix_runtime_job_dispatch_candidate",
        "runtime_job",
        ["target_node_id", "status", "dispatch_reserved_until"],
    )
    op.add_column(
        "workflow_node_execution",
        sa.Column("runtime_job_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "workflow_node_execution",
        sa.Column(
            "phase",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            server_default="run",
            nullable=False,
        ),
    )
    op.add_column(
        "workflow_node_execution",
        sa.Column(
            "pending_payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_workflow_execution_runtime_job",
        "workflow_node_execution",
        "runtime_job",
        ["runtime_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "project_repository",
        sa.Column("validation_job_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_repository_validation_job",
        "project_repository",
        "runtime_job",
        ["validation_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Workflow application metadata is part of an immutable Package version.
    # Rebuild this derived registry from each stored canonical manifest instead
    # of retaining the old template-scoped row that was overwritten on sync.
    op.drop_constraint(
        "uq_workflow_application_route", "workflow_application", type_="unique"
    )
    op.drop_constraint(
        "uq_workflow_application_template", "workflow_application", type_="unique"
    )
    op.add_column(
        "workflow_application",
        sa.Column("template_version_id", sa.Uuid(), nullable=True),
    )
    op.execute(sa.text("DELETE FROM workflow_application"))
    op.execute(
        sa.text(
            """
            INSERT INTO workflow_application (
                id,
                template_id,
                template_version_id,
                component_key,
                route_slug,
                build_digest,
                shell_version
            )
            SELECT
                id,
                template_id,
                id,
                manifest -> 'application' ->> 'component_key',
                manifest -> 'application' ->> 'route_slug',
                manifest -> 'application' ->> 'build_digest',
                manifest -> 'application' ->> 'shell_version'
            FROM workflow_template_version
            """
        )
    )
    op.alter_column("workflow_application", "template_version_id", nullable=False)
    op.create_foreign_key(
        "fk_workflow_application_template_version",
        "workflow_application",
        "workflow_template_version",
        ["template_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_workflow_application_template_version",
        "workflow_application",
        ["template_version_id"],
    )
    op.create_index(
        "ix_workflow_application_template_version_id",
        "workflow_application",
        ["template_version_id"],
    )
    op.drop_constraint(
        "workflow_application_template_id_fkey",
        "workflow_application",
        type_="foreignkey",
    )
    op.drop_column("workflow_application", "template_id")


def downgrade() -> None:
    op.add_column(
        "workflow_application",
        sa.Column("template_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE workflow_application AS application
            SET template_id = version.template_id
            FROM workflow_template_version AS version
            WHERE application.template_version_id = version.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM workflow_application AS application
            USING workflow_template_version AS version
            WHERE application.template_version_id = version.id
              AND application.template_version_id <> (
                  SELECT candidate.id
                  FROM workflow_template_version AS candidate
                  WHERE candidate.template_id = version.template_id
                  ORDER BY candidate.created_at DESC, candidate.id DESC
                  LIMIT 1
              )
            """
        )
    )
    op.drop_index(
        "ix_workflow_application_template_version_id",
        table_name="workflow_application",
    )
    op.drop_constraint(
        "uq_workflow_application_template_version",
        "workflow_application",
        type_="unique",
    )
    op.drop_constraint(
        "fk_workflow_application_template_version",
        "workflow_application",
        type_="foreignkey",
    )
    op.drop_column("workflow_application", "template_version_id")
    op.alter_column("workflow_application", "template_id", nullable=False)
    op.create_foreign_key(
        "workflow_application_template_id_fkey",
        "workflow_application",
        "workflow_template",
        ["template_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_workflow_application_template",
        "workflow_application",
        ["template_id"],
    )
    op.create_unique_constraint(
        "uq_workflow_application_route",
        "workflow_application",
        ["route_slug"],
    )
    op.drop_constraint(
        "fk_project_repository_validation_job",
        "project_repository",
        type_="foreignkey",
    )
    op.drop_column("project_repository", "validation_job_id")
    op.drop_constraint(
        "fk_workflow_execution_runtime_job",
        "workflow_node_execution",
        type_="foreignkey",
    )
    op.drop_column("workflow_node_execution", "pending_payload")
    op.drop_column("workflow_node_execution", "phase")
    op.drop_column("workflow_node_execution", "runtime_job_id")
    op.drop_index("ix_runtime_job_dispatch_candidate", table_name="runtime_job")
    op.drop_index("ix_runtime_job_dispatch_reserved_until", table_name="runtime_job")
    op.drop_index("ix_runtime_job_dispatch_connection_id", table_name="runtime_job")
    op.drop_index("ix_runtime_job_target_node_id", table_name="runtime_job")
    op.drop_index("ix_runtime_job_namespace_id", table_name="runtime_job")
    op.drop_table("runtime_job")
    sa.Enum(name="runtimejobstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="runtimejobkind").drop(op.get_bind(), checkfirst=True)

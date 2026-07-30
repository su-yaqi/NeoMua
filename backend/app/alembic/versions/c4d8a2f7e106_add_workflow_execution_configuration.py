"""add workflow execution configuration

Revision ID: c4d8a2f7e106
Revises: b1e7a4c2d905
Create Date: 2026-07-14 00:40:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d8a2f7e106"
down_revision = "b1e7a4c2d905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "namespace_workflow_configuration",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["workflow_template_version.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_id",
            "template_version_id",
            name="uq_namespace_workflow_configuration_version",
        ),
    )
    op.create_index(
        "ix_namespace_workflow_configuration_namespace_id",
        "namespace_workflow_configuration",
        ["namespace_id"],
    )
    op.create_index(
        "ix_namespace_workflow_configuration_template_version_id",
        "namespace_workflow_configuration",
        ["template_version_id"],
    )
    op.create_table(
        "workflow_execution_configuration_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["namespace_workflow_configuration.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "configuration_id",
            "revision",
            name="uq_workflow_execution_configuration_revision",
        ),
    )
    op.create_index(
        "ix_workflow_execution_configuration_revision_configuration_id",
        "workflow_execution_configuration_revision",
        ["configuration_id"],
    )
    op.create_index(
        "ix_workflow_execution_configuration_revision_project_id",
        "workflow_execution_configuration_revision",
        ["project_id"],
    )
    op.create_foreign_key(
        "fk_namespace_workflow_configuration_current_revision",
        "namespace_workflow_configuration",
        "workflow_execution_configuration_revision",
        ["current_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "workflow_execution_node_binding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("configuration_revision_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=128), nullable=False),
        sa.Column("runtime_profile_id", sa.Uuid(), nullable=False),
        sa.Column("agent_release_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_spec_digest", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["configuration_revision_id"],
            ["workflow_execution_configuration_revision.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"], ["runtime_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["agent_release_id"], ["agent_release.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "configuration_revision_id",
            "node_key",
            name="uq_workflow_execution_configuration_node",
        ),
    )
    op.create_index(
        "ix_workflow_execution_node_binding_configuration_revision_id",
        "workflow_execution_node_binding",
        ["configuration_revision_id"],
    )
    op.add_column(
        "workflow_instance",
        sa.Column("execution_configuration_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflow_instance_execution_configuration_revision",
        "workflow_instance",
        "workflow_execution_configuration_revision",
        ["execution_configuration_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_workflow_instance_execution_configuration_revision_id",
        "workflow_instance",
        ["execution_configuration_revision_id"],
    )
    op.alter_column(
        "workflow_node_instance",
        "resolved_runtime_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "workflow_node_instance",
        "resolved_runtime_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index(
        "ix_workflow_instance_execution_configuration_revision_id",
        table_name="workflow_instance",
    )
    op.drop_constraint(
        "fk_workflow_instance_execution_configuration_revision",
        "workflow_instance",
        type_="foreignkey",
    )
    op.drop_column("workflow_instance", "execution_configuration_revision_id")
    op.drop_index(
        "ix_workflow_execution_node_binding_configuration_revision_id",
        table_name="workflow_execution_node_binding",
    )
    op.drop_table("workflow_execution_node_binding")
    op.drop_constraint(
        "fk_namespace_workflow_configuration_current_revision",
        "namespace_workflow_configuration",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_workflow_execution_configuration_revision_project_id",
        table_name="workflow_execution_configuration_revision",
    )
    op.drop_index(
        "ix_workflow_execution_configuration_revision_configuration_id",
        table_name="workflow_execution_configuration_revision",
    )
    op.drop_table("workflow_execution_configuration_revision")
    op.drop_index(
        "ix_namespace_workflow_configuration_template_version_id",
        table_name="namespace_workflow_configuration",
    )
    op.drop_index(
        "ix_namespace_workflow_configuration_namespace_id",
        table_name="namespace_workflow_configuration",
    )
    op.drop_table("namespace_workflow_configuration")

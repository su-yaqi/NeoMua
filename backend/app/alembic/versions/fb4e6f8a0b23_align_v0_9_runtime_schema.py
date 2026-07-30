"""align v0.9 Runtime schema with domain metadata

Revision ID: fb4e6f8a0b23
Revises: fa3d5e7f9a12
Create Date: 2026-07-17 16:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fb4e6f8a0b23"
down_revision = "fa3d5e7f9a12"
branch_labels = None
depends_on = None


JSON_COLUMNS = {
    "agent_draft": ["execution_policy"],
    "agent_task_model_usage": ["runtime_evidence"],
    "runtime_capability_report": ["capabilities", "discovered_models"],
    "runtime_configuration_revision": [
        "arguments",
        "environment_allowlist",
        "security_policy",
        "resource_limits",
        "error",
    ],
    "runtime_model_binding": ["last_error"],
    "runtime_model_validation_attempt": ["error"],
    "runtime_skill_state": ["last_error"],
    "runtime_skill_sync_attempt": ["error"],
}

INDEXES = {
    "agent_activation_precheck": [
        "namespace_id",
        "release_id",
        "runtime_instance_id",
    ],
    "agent_release_runtime_compatibility": [
        "namespace_id",
        "release_id",
        "runtime_instance_id",
    ],
    "agent_task_model_usage": ["task_id"],
    "conversation_execution_binding": ["configuration_revision_id"],
    "workflow_execution_node_binding": ["runtime_instance_id"],
}


def upgrade() -> None:
    for table, columns in JSON_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                existing_type=sa.JSON(),
                type_=postgresql.JSONB(astext_type=sa.Text()),
                postgresql_using=f"{column}::jsonb",
            )
    for table, columns in INDEXES.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, columns in reversed(list(INDEXES.items())):
        for column in reversed(columns):
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    for table, columns in reversed(list(JSON_COLUMNS.items())):
        for column in reversed(columns):
            op.alter_column(
                table,
                column,
                existing_type=postgresql.JSONB(astext_type=sa.Text()),
                type_=sa.JSON(),
                postgresql_using=f"{column}::json",
            )

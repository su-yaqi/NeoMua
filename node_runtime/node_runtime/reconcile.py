from pydantic import BaseModel, Field


class ReconcileState(BaseModel):
    last_acknowledged_event: int = -1
    interrupted_task_ids: list[str] = Field(default_factory=list)
    spool_first_sequence: int | None = None
    spool_last_sequence: int | None = None
    config_revision: int = 0
    artifact_versions: dict[str, str] = Field(default_factory=dict)

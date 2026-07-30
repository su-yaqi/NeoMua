import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.runtime.models import ModelSelectionMode
from app.workflow_management.models import ConfirmationDecision


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistrySync(StrictBody):
    manifest: dict[str, Any]
    package_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    build_metadata: dict[str, Any] = {}


class EnablementUpdate(StrictBody):
    template_version_id: uuid.UUID
    enabled: bool
    is_default: bool = False


class WorkflowPreflight(StrictBody):
    template_version_id: uuid.UUID


class WorkflowExecutionNodeBindingInput(StrictBody):
    runtime_id: uuid.UUID | None = None
    runtime_instance_id: uuid.UUID | None = None
    agent_release_id: uuid.UUID | None = None
    runtime_agent_release_id: uuid.UUID | None = None
    model_selection_mode: ModelSelectionMode | None = None
    runtime_model_binding_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "WorkflowExecutionNodeBindingInput":
        legacy = self.runtime_id is not None
        current = self.runtime_instance_id is not None
        if legacy == current:
            raise ValueError("submit exactly one Runtime target")
        if (
            self.model_selection_mode == ModelSelectionMode.EXACT
            and self.runtime_model_binding_id is None
        ):
            raise ValueError("exact mode requires runtime_model_binding_id")
        if (
            self.model_selection_mode == ModelSelectionMode.AGENT_PREFERENCE
            and self.runtime_model_binding_id is not None
        ):
            raise ValueError(
                "agent_preference does not accept runtime_model_binding_id"
            )
        if legacy and (
            self.runtime_agent_release_id is not None
            or self.model_selection_mode is not None
            or self.runtime_model_binding_id is not None
        ):
            raise ValueError("Legacy node cannot include v0.9 execution fields")
        return self


class WorkflowExecutionConfigurationUpdate(StrictBody):
    expected_revision: int = Field(ge=0)
    project_id: uuid.UUID | None = None
    node_bindings: dict[str, WorkflowExecutionNodeBindingInput]


class WorkflowInstanceCreate(StrictBody):
    title: str = Field(min_length=1, max_length=255)
    template_version_id: uuid.UUID
    input: dict[str, Any]


class NodeMutation(StrictBody):
    expected_revision: int = Field(ge=0)
    output: dict[str, Any]
    reason: str = Field(min_length=1)


class NodeConfirmation(StrictBody):
    expected_revision: int = Field(ge=1)
    decision: ConfirmationDecision
    reason: str | None = None


class NodeSkip(StrictBody):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1)


class NodeRetry(StrictBody):
    expected_revision: int = Field(ge=0)


class ExternalStateResolution(StrictBody):
    expected_revision: int = Field(ge=0)
    execution_id: uuid.UUID
    conclusion: str = Field(pattern=r"^(not_started|compensated|completed|unknown)$")
    allow_retry: bool = False
    evidence: dict[str, Any] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class NodeMessage(StrictBody):
    expected_revision: int = Field(ge=0)
    content: str = Field(min_length=1)

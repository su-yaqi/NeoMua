from sqlmodel import Session, delete, update

from app.agent_management.models import (
    AgentDefinition,
    AgentDraft,
    HarnessProfile,
)
from app.conversation_management.models import (
    AgentDelegation,
    Conversation,
    ConversationAgent,
    ConversationAttachment,
    ConversationContextSnapshot,
    ConversationMessage,
)
from app.core.config import settings
from app.models import Item, LlmProviderConfig, LlmProviderModel, User
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    ProjectSpecBinding,
    ProjectSpecLocation,
    SpecStandard,
    SpecStandardVersion,
)
from app.runtime.models import (
    AgentEvent,
    AgentSession,
    AgentTask,
    ArtifactDeployment,
    ArtifactRelease,
    LlmProviderModelValidation,
    NodeBootstrapAttempt,
    NodeBootstrapSession,
    NodeCredential,
    NodeDistributionRelease,
    NodeEnrollmentToken,
    NodeHandshakeNonce,
    NodeInstallationReceipt,
    PlatformRuntimeReconcileAttempt,
    PlatformRuntimeReconcileJob,
    RuntimeAdapterRelease,
    RuntimeArtifact,
    RuntimeControlDecision,
    RuntimeDiscoveryObservation,
    RuntimeInstallationMigrationReceipt,
    RuntimeInstance,
    RuntimeModelBinding,
    RuntimeNode,
    RuntimeNodeArtifact,
    RuntimeProfile,
    RuntimeSecret,
)
from app.workflow_management.models import (
    NamespaceWorkflowEnablement,
    WorkflowArtifact,
    WorkflowConfirmation,
    WorkflowEvent,
    WorkflowGateResult,
    WorkflowInstance,
    WorkflowNodeExecution,
    WorkflowNodeInstance,
    WorkflowNodeRevision,
)


def cleanup_test_data(session: Session) -> None:
    session.execute(delete(WorkflowArtifact))
    session.execute(delete(WorkflowConfirmation))
    session.execute(delete(WorkflowGateResult))
    session.execute(delete(WorkflowNodeExecution))
    session.execute(update(WorkflowNodeInstance).values(current_revision_id=None))
    session.execute(delete(WorkflowNodeRevision))
    session.execute(delete(WorkflowNodeInstance))
    session.execute(delete(WorkflowEvent))
    session.execute(delete(WorkflowInstance))
    session.execute(delete(NamespaceWorkflowEnablement))
    session.execute(delete(AgentDelegation))
    session.execute(delete(ConversationAttachment))
    session.execute(delete(ConversationMessage))
    session.execute(update(Conversation).values(current_context_snapshot_id=None))
    session.execute(delete(ConversationContextSnapshot))
    session.execute(delete(ConversationAgent))
    session.execute(delete(Conversation))
    session.execute(delete(ProjectSpecBinding))
    session.execute(delete(ProjectSpecLocation))
    session.execute(delete(ProjectRepository))
    session.execute(delete(ProjectMember))
    session.execute(delete(Project))
    session.execute(delete(SpecStandardVersion))
    session.execute(delete(SpecStandard))
    session.execute(delete(AgentDraft))
    session.execute(delete(AgentDefinition))
    session.execute(delete(HarnessProfile))
    session.execute(delete(AgentEvent))
    session.execute(delete(AgentTask))
    session.execute(delete(AgentSession))
    session.execute(delete(ArtifactDeployment))
    session.execute(delete(RuntimeNodeArtifact))
    session.execute(delete(ArtifactRelease))
    session.execute(delete(RuntimeArtifact))
    session.execute(delete(RuntimeModelBinding))
    session.execute(delete(LlmProviderModelValidation))
    session.execute(delete(PlatformRuntimeReconcileAttempt))
    session.execute(delete(PlatformRuntimeReconcileJob))
    session.execute(delete(RuntimeControlDecision))
    session.execute(delete(RuntimeDiscoveryObservation))
    session.execute(delete(RuntimeInstallationMigrationReceipt))
    session.execute(delete(RuntimeInstance))
    session.execute(update(RuntimeNode).values(current_installation_receipt_id=None))
    session.execute(delete(NodeInstallationReceipt))
    session.execute(delete(NodeBootstrapAttempt))
    session.execute(delete(NodeBootstrapSession))
    session.execute(delete(NodeCredential))
    session.execute(delete(NodeEnrollmentToken))
    session.execute(delete(NodeHandshakeNonce))
    session.execute(delete(RuntimeNode))
    session.execute(delete(RuntimeAdapterRelease))
    session.execute(delete(NodeDistributionRelease))
    session.execute(delete(RuntimeSecret))
    session.execute(delete(RuntimeProfile))
    session.execute(delete(LlmProviderModel))
    session.execute(delete(LlmProviderConfig))
    session.execute(delete(Item))
    session.execute(delete(User).where(User.email != settings.FIRST_SUPERUSER))
    session.commit()

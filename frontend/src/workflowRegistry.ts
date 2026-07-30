import type { ComponentType } from "react"
import { ProjectDeliveryApplication } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryApplication.tsx"
import { ProjectDeliveryApplicationV1_0_3 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryApplicationV1_0_3.tsx"
import { ProjectDeliveryApplicationV1_0_4 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryApplicationV1_0_4.tsx"
import { ProjectDeliveryCreateV1_0_5 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryCreateV1_0_5.tsx"
import { ProjectDeliveryCreateV1_0_6 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryCreateV1_0_6.tsx"
import { ProjectDeliveryTask } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTask.tsx"
import { ProjectDeliveryTaskV1_0_3 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTaskV1_0_3.tsx"
import { ProjectDeliveryTaskV1_0_4 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTaskV1_0_4.tsx"
import { ProjectDeliveryTaskV1_0_5 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTaskV1_0_5.tsx"
import { ProjectDeliveryTaskV1_0_6 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTaskV1_0_6.tsx"
import { WebPlatformDevelopmentCreate } from "../../workflow_apps/web_platform_development/frontend/WebPlatformDevelopmentCreate.tsx"
import { WebPlatformDevelopmentCreateV1_0_1 } from "../../workflow_apps/web_platform_development/frontend/WebPlatformDevelopmentCreateV1_0_1.tsx"
import { WebPlatformDevelopmentTask } from "../../workflow_apps/web_platform_development/frontend/WebPlatformDevelopmentTask.tsx"
import { WebPlatformDevelopmentTaskV1_0_1 } from "../../workflow_apps/web_platform_development/frontend/WebPlatformDevelopmentTaskV1_0_1.tsx"

export interface WorkflowFrontendRegistration {
  Create: ComponentType<WorkflowCreateProps>
  Task: ComponentType<{ instanceId: string }>
}

export interface WorkflowCreateProps {
  workflowSlug: string
  templateId: string
  templateVersionId: string
}

const registry: Record<string, WorkflowFrontendRegistration> = {
  "workflow.project_delivery": {
    Create: ProjectDeliveryApplication,
    Task: ProjectDeliveryTask,
  },
  "workflow.project_delivery.v1_0_3": {
    Create: ProjectDeliveryApplicationV1_0_3,
    Task: ProjectDeliveryTaskV1_0_3,
  },
  "workflow.project_delivery.v1_0_4": {
    Create: ProjectDeliveryApplicationV1_0_4,
    Task: ProjectDeliveryTaskV1_0_4,
  },
  "workflow.project_delivery.v1_0_5": {
    Create: ProjectDeliveryCreateV1_0_5,
    Task: ProjectDeliveryTaskV1_0_5,
  },
  "workflow.project_delivery.v1_0_6": {
    Create: ProjectDeliveryCreateV1_0_6,
    Task: ProjectDeliveryTaskV1_0_6,
  },
  "workflow.project_delivery.v1_0_7": {
    Create: ProjectDeliveryCreateV1_0_6,
    Task: ProjectDeliveryTaskV1_0_6,
  },
  "workflow.web_platform_development.v1_0_0": {
    Create: WebPlatformDevelopmentCreate,
    Task: WebPlatformDevelopmentTask,
  },
  "workflow.web_platform_development.v1_0_1": {
    Create: WebPlatformDevelopmentCreateV1_0_1,
    Task: WebPlatformDevelopmentTaskV1_0_1,
  },
  "workflow.web_platform_development.v1_0_2": {
    Create: WebPlatformDevelopmentCreateV1_0_1,
    Task: WebPlatformDevelopmentTaskV1_0_1,
  },
}

export function resolveWorkflowFrontend(
  componentKey: string,
): WorkflowFrontendRegistration | null {
  return registry[componentKey] ?? null
}

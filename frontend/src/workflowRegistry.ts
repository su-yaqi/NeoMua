import type { ComponentType } from "react"
import { ProjectDeliveryApplication } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryApplication.tsx"
import { ProjectDeliveryApplicationV1_0_3 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryApplicationV1_0_3.tsx"
import { ProjectDeliveryTask } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTask.tsx"
import { ProjectDeliveryTaskV1_0_3 } from "../../workflow_apps/project_delivery/frontend/ProjectDeliveryTaskV1_0_3.tsx"

export interface WorkflowFrontendRegistration {
  Application: ComponentType<{ workflowSlug: string }>
  Task: ComponentType<{ instanceId: string }>
}

const registry: Record<string, WorkflowFrontendRegistration> = {
  "workflow.project_delivery": {
    Application: ProjectDeliveryApplication,
    Task: ProjectDeliveryTask,
  },
  "workflow.project_delivery.v1_0_3": {
    Application: ProjectDeliveryApplicationV1_0_3,
    Task: ProjectDeliveryTaskV1_0_3,
  },
}

export function resolveWorkflowFrontend(
  componentKey: string,
): WorkflowFrontendRegistration | null {
  return registry[componentKey] ?? null
}

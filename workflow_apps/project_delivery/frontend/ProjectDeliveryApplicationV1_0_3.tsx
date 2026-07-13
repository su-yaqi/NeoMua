import { ProjectDeliveryApplication } from "./ProjectDeliveryApplication.tsx"

export const componentKeyV1_0_3 = "workflow.project_delivery.v1_0_3"

export function ProjectDeliveryApplicationV1_0_3({
  workflowSlug,
}: {
  workflowSlug: string
}) {
  return <ProjectDeliveryApplication workflowSlug={workflowSlug} />
}

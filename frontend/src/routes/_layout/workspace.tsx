import { createFileRoute } from "@tanstack/react-router"
import AIWorkspace from "@/components/Workspace/AIWorkspace"

export const Route = createFileRoute("/_layout/workspace")({
  component: () => <AIWorkspace />,
})

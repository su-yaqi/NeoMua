import { createFileRoute } from "@tanstack/react-router"
import AIWorkspace from "@/components/Workspace/AIWorkspace"

export const Route = createFileRoute(
  "/_layout/workspace_/conversations/$conversationId",
)({
  component: ConversationPage,
})

function ConversationPage() {
  const { conversationId } = Route.useParams()
  return <AIWorkspace conversationId={conversationId} />
}

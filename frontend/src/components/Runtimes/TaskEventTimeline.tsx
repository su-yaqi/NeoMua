import type { RuntimeEvent } from "@/api/tenantApi"

export default function TaskEventTimeline({
  events,
}: {
  events: RuntimeEvent[]
}) {
  return (
    <div className="space-y-3">
      {events.map((event) => (
        <div className="rounded-md border p-3" key={event.sequence}>
          <div className="flex items-center justify-between">
            <span className="font-medium">{event.event_type}</span>
            <span className="text-xs text-muted-foreground">
              #{event.sequence}
            </span>
          </div>
          {event.event_type === "tool_call" ||
          event.event_type === "tool_result" ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-sm">查看工具负载</summary>
              <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(event.payload, null, 2)}
              </pre>
            </details>
          ) : (
            <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}

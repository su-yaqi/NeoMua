import { AxiosError } from "axios"

export function extractErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)
      ?.detail
    if (Array.isArray(detail) && detail.length > 0) {
      return String((detail[0] as { msg?: unknown }).msg ?? err.message)
    }
    if (detail && typeof detail === "object") {
      const structured = detail as {
        errors?: Array<{ message?: unknown }>
        current_revision?: unknown
      }
      if (structured.errors?.length) {
        return String(structured.errors[0]?.message ?? err.message)
      }
      if (structured.current_revision !== undefined) {
        return `草稿已被其他人更新（当前 revision ${structured.current_revision}），请重新加载后手工合并。`
      }
    }
    return typeof detail === "string" ? detail : err.message
  }
  return err instanceof Error ? err.message : "Something went wrong."
}

export const handleError = function (
  this: (msg: string) => void,
  err: unknown,
) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}

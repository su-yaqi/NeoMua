import { AxiosError } from "axios"

function extractErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)
      ?.detail
    if (Array.isArray(detail) && detail.length > 0) {
      return String((detail[0] as { msg?: unknown }).msg ?? err.message)
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

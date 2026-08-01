const EXPLICIT_TIME_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * Parse timestamps from the API. New responses carry `Z`; the fallback keeps
 * legacy naive UTC strings correct while old records/cached responses expire.
 */
export function parseApiDateTime(value?: string | null): Date | null {
  if (!value) return null;
  const isoValue = value.trim().replace(" ", "T");
  const normalized = EXPLICIT_TIME_ZONE.test(isoValue)
    ? isoValue
    : `${isoValue}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatLocalDateTime(
  value?: string | null,
  options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  },
  fallback = "—",
): string {
  const parsed = parseApiDateTime(value);
  return parsed
    ? new Intl.DateTimeFormat("zh-CN", options).format(parsed)
    : fallback;
}

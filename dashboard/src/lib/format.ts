/**
 * Display helpers.
 *
 * displayName is the privacy rule made visible: a record's name field is
 * optional because the server prunes it for roles outside the identifier
 * set, and every screen renders through this helper so the code-only view is
 * a designed state, never a blank cell.
 */

const LAGOS = "Africa/Lagos";

export function displayName(
  record: { full_name?: string; given_name?: string },
  code: string,
): string {
  return record.full_name ?? record.given_name ?? code;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-NG", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: LAGOS,
  }).format(new Date(value));
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-NG", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: LAGOS,
  }).format(new Date(value));
}

/** "3 h left" / "5 h overdue" against a deadline, for the alert list. */
export function deadlineLabel(acknowledgeBy: string, now: Date = new Date()): string {
  const deltaHours = (new Date(acknowledgeBy).getTime() - now.getTime()) / 3_600_000;
  const rounded = Math.round(Math.abs(deltaHours) * 10) / 10;
  const amount = rounded >= 48 ? `${Math.round(rounded / 24)} d` : `${rounded} h`;
  return deltaHours >= 0 ? `${amount} left` : `${amount} overdue`;
}

export function formatHours(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 10) / 10} h`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value}%`;
}

/**
 * Today's date in Lagos as YYYY-MM-DD. new Date().toISOString() would give
 * the UTC date, which is yesterday between midnight and 01:00 local time —
 * and a clinical record dated yesterday is a data-quality incident.
 */
export function lagosToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: LAGOS,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

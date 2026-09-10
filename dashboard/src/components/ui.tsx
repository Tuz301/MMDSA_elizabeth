/** Small shared pieces: pills, modal, error banner, empty state. */

import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import type { Severity } from "../api/types";

export function SeverityPill({ severity }: { severity: Severity }) {
  const tone =
    severity === "CRITICAL" ? "urgent" : severity === "HIGH" ? "warn" : "muted";
  return <span className={`pill ${tone}`}>{severity}</span>;
}

const STATUS_TONES: Record<string, string> = {
  // Alert lifecycle.
  OPEN: "urgent",
  ESCALATED: "urgent",
  ACKNOWLEDGED: "warn",
  RESOLVED: "ok",
  CANCELLED: "muted",
  // Results.
  POSITIVE: "urgent",
  NEGATIVE: "ok",
  INDETERMINATE: "warn",
  REJECTED: "muted",
  PENDING: "neutral",
  // Linkage.
  STARTED: "ok",
  REFUSED: "warn",
  UNREACHABLE: "warn",
  // Location verdicts.
  VERIFIED: "ok",
  OUT_OF_RANGE: "warn",
  LOW_ACCURACY: "muted",
  NO_FIX: "muted",
  NO_REFERENCE: "muted",
  // Appointments and generic states.
  SCHEDULED: "neutral",
  ATTENDED: "ok",
  MISSED: "warn",
  RESCHEDULED: "neutral",
  ACCEPTED: "ok",
  PARTIAL: "warn",
};

export function StatusPill({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`pill ${STATUS_TONES[value] ?? "muted"}`}>
      {label ?? value.replaceAll("_", " ")}
    </span>
  );
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const message =
    error instanceof ApiError || error instanceof Error
      ? error.message
      : "Something went wrong.";
  return <div className="banner-error" role="alert">{message}</div>;
}

export function FieldError({ error, field }: { error: unknown; field: string }) {
  if (!(error instanceof ApiError)) return null;
  const messages = error.fieldErrors[field];
  if (!messages) return null;
  return <div className="field-error">{messages.join(" ")}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}

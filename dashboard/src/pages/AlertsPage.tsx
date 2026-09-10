/**
 * The alert worklist.
 *
 * Acknowledge is one click: it records "a person has seen this" and stops the
 * escalation clock, so nothing may stand between the supervisor and it.
 * Resolve requires a note, because the server refuses an empty resolution —
 * an alert closed with no reason is indistinguishable from one dismissed.
 */

import { useState } from "react";
import { Link, useOutletContext } from "react-router-dom";

import { useAcknowledgeAlert, useAlerts, useResolveAlert } from "../api/hooks";
import type { Alert, Me } from "../api/types";
import { deadlineLabel, formatDateTime } from "../lib/format";
import {
  Empty,
  ErrorBanner,
  Modal,
  SeverityPill,
  StatusPill,
  TruncationNote,
} from "../components/ui";

/** Where the thing an alert concerns can be acted on. */
function subjectRoute(subjectType: string): string | null {
  if (subjectType.startsWith("eid.") || subjectType === "registry.Infant") {
    return "/eid";
  }
  if (subjectType === "visits.HomeVisit") return "/visits";
  return null;
}

//: The default view is everything still demanding action. ESCALATED is the
//: most urgent state an alert can be in — it already missed its deadline
//: once — and a default that hid it would bury exactly the wrong rows.
const VIEWS: [string, string, Record<string, string>][] = [
  ["actionable", "Open & escalated", { status__in: "OPEN,ESCALATED" }],
  ["acknowledged", "Acknowledged", { status: "ACKNOWLEDGED" }],
  ["resolved", "Resolved", { status: "RESOLVED" }],
  ["all", "All", {}],
];

export function AlertsPage() {
  const me = useOutletContext<Me>();
  const [view, setView] = useState<string>("actionable");
  const alerts = useAlerts(VIEWS.find(([key]) => key === view)?.[2] ?? {});
  const acknowledge = useAcknowledgeAlert();
  const resolve = useResolveAlert();
  const [resolving, setResolving] = useState<Alert | null>(null);
  const [note, setNote] = useState("");

  const rows = alerts.data?.results ?? [];

  return (
    <>
      <h1>Alerts</h1>
      <p className="subtitle">
        Ordered by severity, nearest deadline first. An unanswered alert
        escalates on its own.
      </p>
      <ErrorBanner error={alerts.error ?? acknowledge.error ?? resolve.error} />

      <div className="toolbar">
        <label htmlFor="status-filter" style={{ margin: 0 }}>View</label>
        <select
          id="status-filter"
          value={view}
          onChange={(event) => setView(event.target.value)}
        >
          {VIEWS.map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
      </div>

      {rows.length === 0 ? (
        <Empty>Nothing here.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>What</th>
                <th>Assigned</th>
                <th>Raised</th>
                <th>Deadline</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((alert) => (
                <tr key={alert.id} className={alert.is_past_deadline ? "row-urgent" : ""}>
                  <td><SeverityPill severity={alert.severity} /></td>
                  <td>
                    {alert.title}
                    {alert.detail && (
                      <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>
                        {alert.detail}
                      </div>
                    )}
                    {subjectRoute(alert.subject_type) && (
                      <Link
                        to={subjectRoute(alert.subject_type)!}
                        style={{ fontSize: 13 }}
                      >
                        Open the worklist
                      </Link>
                    )}
                  </td>
                  <td className="code">{alert.assigned_mentor_mother ?? "—"}</td>
                  <td>{formatDateTime(alert.raised_at)}</td>
                  <td>
                    {["OPEN", "ESCALATED"].includes(alert.status)
                      ? deadlineLabel(alert.acknowledge_by)
                      : "—"}
                  </td>
                  <td>
                    <StatusPill value={alert.status} />
                    {alert.acknowledgement_channel === "SMS" && (
                      <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>by SMS reply</div>
                    )}
                  </td>
                  <td>
                    {["OPEN", "ESCALATED"].includes(alert.status) && (
                      <button
                        className="subtle"
                        disabled={acknowledge.isPending}
                        onClick={() => acknowledge.mutate(alert.id)}
                      >
                        Acknowledge
                      </button>
                    )}{" "}
                    {alert.status === "ACKNOWLEDGED" && me.may_enter_clinical_data && (
                      <button
                        className="subtle"
                        onClick={() => {
                          setNote("");
                          setResolving(alert);
                        }}
                      >
                        Resolve…
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <TruncationNote shown={rows.length} total={alerts.data?.count} />
        </div>
      )}

      {resolving && (
        <Modal title="Resolve alert" onClose={() => setResolving(null)}>
          <p style={{ marginTop: 0 }}>{resolving.title}</p>
          <div className="field">
            <label htmlFor="resolution-note">
              What was done? This is read at the monthly review.
            </label>
            <textarea
              id="resolution-note"
              rows={3}
              style={{ width: "100%" }}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          <div className="actions">
            <button className="secondary" onClick={() => setResolving(null)}>
              Cancel
            </button>
            <button
              disabled={!note.trim() || resolve.isPending}
              onClick={() =>
                resolve.mutate(
                  { id: resolving.id, note: note.trim() },
                  { onSuccess: () => setResolving(null) },
                )
              }
            >
              Resolve
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

/**
 * The overview.
 *
 * The first number on the screen is the number the system exists to drive to
 * zero: positive results nobody has acknowledged, right now. Everything else
 * is context. The tile links straight into the worklist, because a number
 * you cannot act on from where you see it is a poster, not a dashboard.
 */

import { Link } from "react-router-dom";

import { useAlerts, useMetricsSummary } from "../api/hooks";
import { deadlineLabel, formatHours, formatPercent } from "../lib/format";
import {
  Empty,
  ErrorBanner,
  SeverityPill,
  StatusPill,
  TruncationNote,
} from "../components/ui";

const VERDICT_WORDS: Record<string, string> = {
  OUT_OF_RANGE: "out of range",
  LOW_ACCURACY: "imprecise fix",
  NO_FIX: "no position",
  NO_REFERENCE: "no household point on record",
};

export function OverviewPage() {
  const summary = useMetricsSummary();
  const alerts = useAlerts({ status__in: "OPEN,ESCALATED" });

  const s = summary.data;
  const unacknowledged = s?.eid_cascade.unacknowledged_positives_now ?? 0;

  return (
    <>
      <h1>Overview</h1>
      <p className="subtitle">
        {s ? `Window ${s.window.date_from} to ${s.window.date_to}.` : "Loading…"}
      </p>
      <ErrorBanner error={summary.error} />

      <div className="stat-row">
        <Link to="/eid" style={{ textDecoration: "none", color: "inherit" }}>
          <div className={`card stat ${unacknowledged > 0 ? "urgent" : "ok"}`}>
            <div className="stat-label">Unacknowledged positive results, now</div>
            <div className="stat-value">{unacknowledged}</div>
            <div className="stat-note">
              {unacknowledged > 0
                ? "Open the EID worklist and acknowledge."
                : "Every positive result has been seen."}
            </div>
          </div>
        </Link>
        <div className="card stat">
          <div className="stat-label">Relay delay, median (positives)</div>
          <div className="stat-value">
            {formatHours(s?.eid_cascade.relay_delay_median_hours_positive)}
          </div>
          <div className="stat-note">Result entered → acknowledged.</div>
        </div>
        <div className="card stat">
          <div className="stat-label">
            Linked within {s?.art_linkage.linkage_target_days ?? 14} days
          </div>
          <div className="stat-value">
            {formatPercent(s?.art_linkage.pct_linked_within_target)}
          </div>
          <div className="stat-note">
            Cohort of {s?.art_linkage.cohort_size ?? 0}
            {s && s.art_linkage.linkages_missing_issue_date > 0
              ? ` · ${s.art_linkage.linkages_missing_issue_date} missing a lab issue date`
              : ""}
          </div>
        </div>
        <div
          className={`card stat ${(s?.alerts.past_deadline_now ?? 0) > 0 ? "warn" : ""}`}
        >
          <div className="stat-label">Alerts past their deadline, now</div>
          <div className="stat-value">{s?.alerts.past_deadline_now ?? "—"}</div>
          <div className="stat-note">
            SLA compliance {formatPercent(s?.alerts.sla_compliance_pct)} of{" "}
            {s?.alerts.raised ?? 0} raised.
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label">Home visits location-verified</div>
          <div className="stat-value">
            {formatPercent(s?.location_verification.verified_pct)}
          </div>
          <div className="stat-note">
            Target {s?.location_verification.target_pct ?? 85}% ·{" "}
            {s?.location_verification.visits ?? 0} visits.
            {/* The breakdown, because a bare percentage invites reading an
                unverified visit as misconduct when most are honest failures. */}
            {s &&
              Object.entries(s.location_verification.verdicts)
                .filter(([verdict]) => verdict !== "VERIFIED")
                .map(([verdict, count]) => (
                  <div key={verdict}>
                    {count} {VERDICT_WORDS[verdict] ?? verdict.toLowerCase()}
                  </div>
                ))}
          </div>
        </div>
        <div
          className={`card stat ${(s?.sms.delivery_failure_pct ?? 0) > 5 ? "warn" : ""}`}
        >
          <div className="stat-label">SMS delivery failure</div>
          <div className="stat-value">{formatPercent(s?.sms.delivery_failure_pct)}</div>
          <div className="stat-note">
            {s?.sms.dispatch_attempted ?? 0} dispatched
            {s && s.sms.blocked_by_privacy_guard > 0
              ? ` · ${s.sms.blocked_by_privacy_guard} blocked by the privacy guard`
              : ""}
          </div>
        </div>
      </div>

      <h2>Open alerts</h2>
      <ErrorBanner error={alerts.error} />
      {!alerts.data ? (
        <p className="subtitle">Loading alerts…</p>
      ) : alerts.data.results.length === 0 ? (
        <Empty>No open alerts. The registers are quiet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>What</th>
                <th>Deadline</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {alerts.data.results.slice(0, 8).map((alert) => (
                <tr key={alert.id} className={alert.is_past_deadline ? "row-urgent" : ""}>
                  <td><SeverityPill severity={alert.severity} /></td>
                  <td>
                    <Link to="/alerts">{alert.title}</Link>
                  </td>
                  <td>{deadlineLabel(alert.acknowledge_by)}</td>
                  <td><StatusPill value={alert.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <TruncationNote
            shown={Math.min(8, alerts.data.results.length)}
            total={alerts.data.count}
          />
        </div>
      )}
    </>
  );
}

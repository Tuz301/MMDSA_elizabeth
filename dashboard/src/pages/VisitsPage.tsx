/**
 * Home visits: the review queue and the sync backlog.
 *
 * The wording carries the programme's rule: an unverified visit is a prompt
 * for a conversation, never a finding of misconduct. The review reason the
 * server composed says "confirm the household point is correct", and this
 * screen does not editorialise beyond it.
 */

import { useState } from "react";
import { useOutletContext } from "react-router-dom";

import { useReviewVisit, useSyncBatches, useVisits } from "../api/hooks";
import type { HomeVisit, Me } from "../api/types";
import { formatDate, formatDateTime } from "../lib/format";
import { Empty, ErrorBanner, Modal, StatusPill } from "../components/ui";

export function VisitsPage() {
  const me = useOutletContext<Me>();
  const [tab, setTab] = useState<"review" | "all" | "sync">("review");

  return (
    <>
      <h1>Home visits</h1>
      <p className="subtitle">
        The location verdict is computed on the server. A flagged visit is
        usually genuine — a wrong household point, a weak fix under a roof.
      </p>

      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "review"}
          className={tab === "review" ? "active" : ""}
          onClick={() => setTab("review")}
        >
          Needs review
        </button>
        <button
          role="tab"
          aria-selected={tab === "all"}
          className={tab === "all" ? "active" : ""}
          onClick={() => setTab("all")}
        >
          All visits
        </button>
        <button
          role="tab"
          aria-selected={tab === "sync"}
          className={tab === "sync" ? "active" : ""}
          onClick={() => setTab("sync")}
        >
          Handset sync
        </button>
      </div>

      {tab === "review" && <VisitsTable filters={{ flagged_for_review: "true" }} me={me} reviewMode />}
      {tab === "all" && <VisitsTable filters={{}} me={me} />}
      {tab === "sync" && <SyncTab />}
    </>
  );
}

function VisitsTable({
  filters,
  me,
  reviewMode = false,
}: {
  filters: Record<string, string>;
  me: Me;
  reviewMode?: boolean;
}) {
  const visits = useVisits(filters);
  const review = useReviewVisit();
  const [reviewing, setReviewing] = useState<HomeVisit | null>(null);
  const [outcome, setOutcome] = useState("");

  const rows = (visits.data?.results ?? []).filter(
    (visit) => !reviewMode || visit.reviewed_at === null,
  );

  return (
    <>
      <ErrorBanner error={visits.error ?? review.error} />
      {rows.length === 0 ? (
        <Empty>
          {reviewMode ? "No visits are waiting for review." : "No visits in your scope yet."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Client</th>
                <th>Mentor mother</th>
                <th>Purpose</th>
                <th>Result</th>
                <th>Location</th>
                <th>{reviewMode ? "Why flagged" : "Review"}</th>
                {reviewMode && me.may_enter_clinical_data && <th></th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((visit) => (
                <tr key={visit.id}>
                  <td>{formatDate(visit.visit_date)}</td>
                  <td className="code">{visit.client}</td>
                  <td className="code">{visit.mentor_mother}</td>
                  <td>{visit.purpose.replaceAll("_", " ")}</td>
                  <td>{visit.result.replaceAll("_", " ")}</td>
                  <td>
                    <StatusPill value={visit.location_status} />
                    {visit.distance_from_household_metres !== null && (
                      <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>
                        {Math.round(visit.distance_from_household_metres)} m from household
                      </div>
                    )}
                  </td>
                  <td style={{ maxWidth: 280 }}>
                    {reviewMode
                      ? visit.review_reason
                      : visit.reviewed_at
                        ? `${visit.review_outcome} (${formatDate(visit.reviewed_at)})`
                        : visit.flagged_for_review
                          ? "awaiting review"
                          : "—"}
                  </td>
                  {reviewMode && me.may_enter_clinical_data && (
                    <td>
                      <button
                        className="subtle"
                        onClick={() => {
                          setOutcome("");
                          setReviewing(visit);
                        }}
                      >
                        Record review…
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reviewing && (
        <Modal title="Record the review" onClose={() => setReviewing(null)}>
          <p style={{ marginTop: 0, color: "var(--ink-soft)" }}>
            {reviewing.review_reason ||
              "Speak with the mentor mother first. An unverified visit is usually genuine."}
          </p>
          <div className="field">
            <label htmlFor="review-outcome">What you found</label>
            <textarea
              id="review-outcome"
              rows={3}
              style={{ width: "100%" }}
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
            />
          </div>
          <div className="actions">
            <button className="secondary" onClick={() => setReviewing(null)}>
              Cancel
            </button>
            <button
              disabled={!outcome.trim() || review.isPending}
              onClick={() =>
                review.mutate(
                  { id: reviewing.id, review_outcome: outcome.trim() },
                  { onSuccess: () => setReviewing(null) },
                )
              }
            >
              Save review
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function SyncTab() {
  const batches = useSyncBatches();
  const rows = batches.data?.results ?? [];

  return (
    <>
      <ErrorBanner error={batches.error} />
      <p className="subtitle">
        A handset that has not synchronised is holding visit records nobody
        can act on.
      </p>
      {rows.length === 0 ? (
        <Empty>No synchronisation batches yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Device</th>
                <th>Status</th>
                <th>Accepted</th>
                <th>Rejected</th>
                <th>Backlog</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((batch) => (
                <tr key={batch.id} className={(batch.backlog_hours ?? 0) > 72 ? "row-urgent" : ""}>
                  <td>{formatDateTime(batch.created_at)}</td>
                  <td>{batch.user}</td>
                  <td className="code">{batch.device_id.slice(0, 12)}</td>
                  <td><StatusPill value={batch.status} /></td>
                  <td>{batch.records_accepted}/{batch.records_submitted}</td>
                  <td>{batch.records_rejected || "—"}</td>
                  <td>
                    {batch.backlog_hours !== null
                      ? `${Math.round(batch.backlog_hours)} h`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * The EID cascade worklist: samples, appointments, treatment linkage.
 *
 * The screen mirrors the cascade the backend measures. Entry and
 * acknowledgement are separate buttons on purpose — the gap between them is
 * the relay delay, the pilot's primary indicator, and the interface must not
 * blur what the measurement depends on. The server orders samples with the
 * oldest unacknowledged positive first, and this screen trusts that order.
 */

import { useState } from "react";

import {
  useAcknowledgeSample,
  useAppointmentAction,
  useAppointments,
  useCaregiverInformed,
  useEnterResult,
  useLinkages,
  useRegisterSample,
  useSamples,
  useUpdateLinkage,
} from "../api/hooks";
import type { ArtLinkage, EidAppointment, EidSample } from "../api/types";
import { formatDate, formatDateTime, formatHours, lagosToday } from "../lib/format";
import {
  Empty,
  ErrorBanner,
  FieldError,
  Modal,
  StatusPill,
  TruncationNote,
} from "../components/ui";

type Tab = "samples" | "appointments" | "linkages";

export function EidPage() {
  const [tab, setTab] = useState<Tab>("samples");

  return (
    <>
      <h1>EID cascade</h1>
      <p className="subtitle">
        Sample collected → result entered → acknowledged → caregiver informed →
        treatment started. Every step is time-stamped on the server.
      </p>

      <div className="tabs" role="tablist">
        {(
          [
            ["samples", "Samples & results"],
            ["appointments", "Appointments"],
            ["linkages", "Treatment linkage"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "samples" && <SamplesTab />}
      {tab === "appointments" && <AppointmentsTab />}
      {tab === "linkages" && <LinkagesTab />}
    </>
  );
}

// -- Samples ----------------------------------------------------------------

function SamplesTab() {
  const samples = useSamples({});
  const acknowledge = useAcknowledgeSample();
  const caregiverInformed = useCaregiverInformed();
  const [entering, setEntering] = useState<EidSample | null>(null);
  const [confirming, setConfirming] = useState<{
    kind: "acknowledge" | "informed";
    sample: EidSample;
  } | null>(null);

  const rows = samples.data?.results ?? [];

  return (
    <>
      <ErrorBanner
        error={samples.error ?? acknowledge.error ?? caregiverInformed.error}
      />
      {rows.length === 0 ? (
        <Empty>No samples in your scope yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Infant</th>
                <th>Sample</th>
                <th>Collected</th>
                <th>Result</th>
                <th>Entered</th>
                <th>Relay delay</th>
                <th>Next step</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((sample) => (
                <tr
                  key={sample.id}
                  className={sample.is_unacknowledged_positive ? "row-urgent" : ""}
                >
                  <td className="code">{sample.baby_code}</td>
                  <td className="code">{sample.sample_identifier}</td>
                  <td>{formatDate(sample.collected_on)}</td>
                  <td><StatusPill value={sample.result} /></td>
                  <td>{formatDateTime(sample.result_entered_at)}</td>
                  <td>
                    {sample.result_acknowledged_at
                      ? formatHours(sample.hours_entry_to_acknowledgement)
                      : sample.result_entered_at
                        ? "running…"
                        : "—"}
                  </td>
                  <td><NextStep
                    sample={sample}
                    onEnter={() => setEntering(sample)}
                    onAcknowledge={() =>
                      setConfirming({ kind: "acknowledge", sample })
                    }
                    onCaregiverInformed={() =>
                      setConfirming({ kind: "informed", sample })
                    }
                    busy={acknowledge.isPending || caregiverInformed.isPending}
                  /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <TruncationNote shown={rows.length} total={samples.data?.count} />
        </div>
      )}

      {entering && (
        <ResultEntryModal sample={entering} onClose={() => setEntering(null)} />
      )}

      {confirming && (
        <Modal
          title={
            confirming.kind === "acknowledge"
              ? "Acknowledge this result"
              : "Caregiver informed"
          }
          onClose={() => setConfirming(null)}
        >
          <p style={{ marginTop: 0 }}>
            {confirming.kind === "acknowledge" ? (
              <>
                Acknowledge the{" "}
                <strong>{confirming.sample.result}</strong> result for{" "}
                <span className="code">{confirming.sample.baby_code}</span>?
                The time of acknowledgement is recorded once and cannot be
                changed.
              </>
            ) : (
              <>
                Record that the caregiver of{" "}
                <span className="code">{confirming.sample.baby_code}</span> has
                been told the result? This is recorded once and cannot be
                changed.
              </>
            )}
          </p>
          <div className="actions">
            <button className="secondary" onClick={() => setConfirming(null)}>
              Cancel
            </button>
            <button
              disabled={acknowledge.isPending || caregiverInformed.isPending}
              onClick={() => {
                const { kind, sample } = confirming;
                const mutation =
                  kind === "acknowledge" ? acknowledge : caregiverInformed;
                mutation.mutate(sample.id, {
                  onSuccess: () => setConfirming(null),
                });
              }}
            >
              Confirm
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function NextStep({
  sample,
  onEnter,
  onAcknowledge,
  onCaregiverInformed,
  busy,
}: {
  sample: EidSample;
  onEnter: () => void;
  onAcknowledge: () => void;
  onCaregiverInformed: () => void;
  busy: boolean;
}) {
  if (sample.result === "PENDING") {
    return <button className="subtle" onClick={onEnter}>Enter result…</button>;
  }
  if (!sample.result_acknowledged_at) {
    return (
      <button disabled={busy} onClick={onAcknowledge}>
        Acknowledge
      </button>
    );
  }
  if (!sample.caregiver_informed_at) {
    return (
      <button className="subtle" disabled={busy} onClick={onCaregiverInformed}>
        Caregiver informed
      </button>
    );
  }
  return <span className="pill ok">Complete</span>;
}

function ResultEntryModal({
  sample,
  onClose,
}: {
  sample: EidSample;
  onClose: () => void;
}) {
  const enter = useEnterResult();
  const [result, setResult] = useState("NEGATIVE");
  const [issuedOn, setIssuedOn] = useState("");
  const [laboratory, setLaboratory] = useState("");

  return (
    <Modal title={`Enter result for ${sample.baby_code}`} onClose={onClose}>
      <p style={{ marginTop: 0, color: "var(--ink-soft)" }}>
        Entering the result starts the acknowledgement clock. Acknowledge is a
        separate step: the gap between the two is the relay delay the pilot
        measures.
      </p>
      <ErrorBanner error={enter.error} />
      <div className="field">
        <label htmlFor="result">Laboratory result</label>
        <select
          id="result"
          value={result}
          onChange={(event) => setResult(event.target.value)}
        >
          <option value="NEGATIVE">HIV not detected</option>
          <option value="POSITIVE">HIV detected</option>
          <option value="INDETERMINATE">Indeterminate, repeat required</option>
          <option value="REJECTED">Sample rejected by the laboratory</option>
        </select>
        <FieldError error={enter.error} field="result" />
      </div>
      <div className="field">
        <label htmlFor="issued-on">Date the laboratory issued it</label>
        <input
          id="issued-on"
          type="date"
          value={issuedOn}
          onChange={(event) => setIssuedOn(event.target.value)}
        />
        <FieldError error={enter.error} field="result_issued_on" />
      </div>
      <div className="field">
        <label htmlFor="laboratory">Laboratory (optional)</label>
        <input
          id="laboratory"
          value={laboratory}
          onChange={(event) => setLaboratory(event.target.value)}
        />
      </div>
      <div className="actions">
        <button className="secondary" onClick={onClose}>Cancel</button>
        <button
          disabled={!issuedOn || enter.isPending}
          onClick={() =>
            enter.mutate(
              {
                id: sample.id,
                result,
                result_issued_on: issuedOn,
                laboratory_name: laboratory || undefined,
              },
              { onSuccess: onClose },
            )
          }
        >
          Save result
        </button>
      </div>
    </Modal>
  );
}

// -- Appointments -----------------------------------------------------------

const APPOINTMENT_VIEWS: [string, string, Record<string, string>][] = [
  ["overdue", "Overdue", { overdue: "true" }],
  ["upcoming", "Scheduled", { status__in: "SCHEDULED,RESCHEDULED" }],
  ["missed", "Missed", { status: "MISSED" }],
];

function AppointmentsTab() {
  const [view, setView] = useState("overdue");
  const filters =
    APPOINTMENT_VIEWS.find(([key]) => key === view)?.[2] ?? {};
  const appointments = useAppointments(filters);
  const act = useAppointmentAction();
  const [recordingSample, setRecordingSample] = useState<EidAppointment | null>(null);
  const [rescheduling, setRescheduling] = useState<EidAppointment | null>(null);
  const [confirmingMissed, setConfirmingMissed] = useState<EidAppointment | null>(null);
  const rows = appointments.data?.results ?? [];

  return (
    <>
      <ErrorBanner error={appointments.error ?? act.error} />
      <div className="toolbar">
        <label htmlFor="appointment-view" style={{ margin: 0 }}>View</label>
        <select
          id="appointment-view"
          value={view}
          onChange={(event) => setView(event.target.value)}
        >
          {APPOINTMENT_VIEWS.map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
      </div>
      {rows.length === 0 ? (
        <Empty>
          {view === "overdue"
            ? "No overdue appointments. That is the goal."
            : "Nothing in this view."}
        </Empty>
      ) : (
        <>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Infant</th>
                <th>Milestone</th>
                <th>Due</th>
                <th>Days overdue</th>
                <th>Status</th>
                <th>Record</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((appointment) => (
                <tr
                  key={appointment.id}
                  className={appointment.days_overdue > 0 ? "row-urgent" : ""}
                >
                  <td className="code">{appointment.infant}</td>
                  <td>{appointment.milestone.replaceAll("_", " ")}</td>
                  <td>{formatDate(appointment.due_date)}</td>
                  <td>{appointment.days_overdue || "—"}</td>
                  <td><StatusPill value={appointment.status} /></td>
                  <td>
                    {["SCHEDULED", "RESCHEDULED"].includes(appointment.status) && (
                      <>
                        <button
                          className="subtle"
                          onClick={() => setRecordingSample(appointment)}
                        >
                          Record sample…
                        </button>{" "}
                        <button
                          className="subtle"
                          disabled={act.isPending}
                          onClick={() => setConfirmingMissed(appointment)}
                        >
                          Missed…
                        </button>{" "}
                      </>
                    )}
                    {["SCHEDULED", "RESCHEDULED", "MISSED"].includes(
                      appointment.status,
                    ) && (
                      <button
                        className="subtle"
                        onClick={() => setRescheduling(appointment)}
                      >
                        Reschedule…
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <TruncationNote shown={rows.length} total={appointments.data?.count} />
        </>
      )}

      {recordingSample && (
        <RegisterSampleModal
          appointment={recordingSample}
          onClose={() => setRecordingSample(null)}
        />
      )}

      {rescheduling && (
        <RescheduleModal
          appointment={rescheduling}
          onClose={() => setRescheduling(null)}
        />
      )}

      {confirmingMissed && (
        <Modal title="Mark this appointment missed" onClose={() => setConfirmingMissed(null)}>
          <p style={{ marginTop: 0 }}>
            Mark the{" "}
            {confirmingMissed.milestone.replaceAll("_", " ").toLowerCase()} test
            for <span className="code">{confirmingMissed.infant}</span> as
            missed? The engine will prompt a follow-up, and the appointment can
            be rescheduled afterwards.
          </p>
          <div className="actions">
            <button className="secondary" onClick={() => setConfirmingMissed(null)}>
              Cancel
            </button>
            <button
              disabled={act.isPending}
              onClick={() =>
                act.mutate(
                  { id: confirmingMissed.id, action: "missed" },
                  { onSuccess: () => setConfirmingMissed(null) },
                )
              }
            >
              Mark missed
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function RegisterSampleModal({
  appointment,
  onClose,
}: {
  appointment: EidAppointment;
  onClose: () => void;
}) {
  const register = useRegisterSample();
  const [sampleId, setSampleId] = useState("");
  const [collectedOn, setCollectedOn] = useState(lagosToday());
  const [laboratory, setLaboratory] = useState("");

  return (
    <Modal
      title={`Record the sample for ${appointment.infant}`}
      onClose={onClose}
    >
      <p style={{ marginTop: 0, color: "var(--ink-soft)" }}>
        Attendance is recorded through the sample: registering the dried
        blood spot marks the{" "}
        {appointment.milestone.replaceAll("_", " ").toLowerCase()} appointment
        attended and starts the laboratory clock.
      </p>
      <ErrorBanner error={register.error} />
      <div className="field">
        <label htmlFor="sample-id">Sample identifier (from the DBS card)</label>
        <input
          id="sample-id"
          value={sampleId}
          onChange={(event) => setSampleId(event.target.value)}
        />
        <FieldError error={register.error} field="sample_identifier" />
      </div>
      <div className="field">
        <label htmlFor="collected-on">Collected on</label>
        <input
          id="collected-on"
          type="date"
          value={collectedOn}
          onChange={(event) => setCollectedOn(event.target.value)}
        />
        <FieldError error={register.error} field="collected_on" />
      </div>
      <div className="field">
        <label htmlFor="sample-laboratory">Laboratory (optional)</label>
        <input
          id="sample-laboratory"
          value={laboratory}
          onChange={(event) => setLaboratory(event.target.value)}
        />
      </div>
      <div className="actions">
        <button className="secondary" onClick={onClose}>Cancel</button>
        <button
          disabled={!sampleId.trim() || !collectedOn || register.isPending}
          onClick={() =>
            register.mutate(
              {
                appointment: appointment.id,
                sample_identifier: sampleId.trim(),
                collected_on: collectedOn,
                laboratory_name: laboratory.trim() || undefined,
              },
              { onSuccess: onClose },
            )
          }
        >
          Record sample
        </button>
      </div>
    </Modal>
  );
}

function RescheduleModal({
  appointment,
  onClose,
}: {
  appointment: EidAppointment;
  onClose: () => void;
}) {
  const act = useAppointmentAction();
  const [dueDate, setDueDate] = useState("");

  return (
    <Modal title={`Reschedule for ${appointment.infant}`} onClose={onClose}>
      <ErrorBanner error={act.error} />
      <div className="field">
        <label htmlFor="new-due-date">New appointment date</label>
        <input
          id="new-due-date"
          type="date"
          value={dueDate}
          onChange={(event) => setDueDate(event.target.value)}
        />
      </div>
      <div className="actions">
        <button className="secondary" onClick={onClose}>Cancel</button>
        <button
          disabled={!dueDate || act.isPending}
          onClick={() =>
            act.mutate(
              {
                id: appointment.id,
                action: "reschedule",
                body: { due_date: dueDate },
              },
              { onSuccess: onClose },
            )
          }
        >
          Reschedule
        </button>
      </div>
    </Modal>
  );
}

// -- Linkages ---------------------------------------------------------------

function LinkagesTab() {
  // REFUSED and UNREACHABLE stay in the default view: those infants are
  // still awaiting treatment, and a recorded refusal must not remove a
  // child from the only worklist tracking them.
  const linkages = useLinkages({ status__in: "PENDING,REFUSED,UNREACHABLE" });
  const [recording, setRecording] = useState<ArtLinkage | null>(null);
  const rows = linkages.data?.results ?? [];

  return (
    <>
      <ErrorBanner error={linkages.error} />
      <p className="subtitle">
        Every infant here has a positive result and is not yet on treatment —
        including where the caregiver declined or could not be reached. Days
        outstanding counts from the laboratory issue date.
      </p>
      {rows.length === 0 ? (
        <Empty>No infant is awaiting treatment linkage.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Infant</th>
                <th>Days outstanding</th>
                <th>Status</th>
                <th>Record</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((linkage) => (
                <tr
                  key={linkage.id}
                  className={(linkage.days_outstanding ?? 0) > 14 ? "row-urgent" : ""}
                >
                  <td className="code">{linkage.infant}</td>
                  <td>{linkage.days_outstanding ?? "—"}</td>
                  <td><StatusPill value={linkage.status} /></td>
                  <td>
                    <button className="subtle" onClick={() => setRecording(linkage)}>
                      Record outcome…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {recording && (
        <LinkageModal linkage={recording} onClose={() => setRecording(null)} />
      )}
    </>
  );
}

function LinkageModal({
  linkage,
  onClose,
}: {
  linkage: ArtLinkage;
  onClose: () => void;
}) {
  const update = useUpdateLinkage();
  const [status, setStatus] = useState("STARTED");
  const [startDate, setStartDate] = useState("");
  const [regimen, setRegimen] = useState("");
  const [barrierNote, setBarrierNote] = useState("");

  const needsDate = status === "STARTED";
  const needsNote = status === "REFUSED" || status === "UNREACHABLE";

  return (
    <Modal title={`Treatment linkage for ${linkage.infant}`} onClose={onClose}>
      <ErrorBanner error={update.error} />
      <div className="field">
        <label htmlFor="linkage-status">What happened</label>
        <select
          id="linkage-status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="STARTED">Treatment started</option>
          <option value="REFUSED">Caregiver declined</option>
          <option value="UNREACHABLE">Caregiver could not be reached</option>
          <option value="TRANSFERRED">Referred to another facility</option>
          <option value="DECEASED">Infant died before starting</option>
        </select>
        <FieldError error={update.error} field="status" />
      </div>
      {needsDate && (
        <>
          <div className="field">
            <label htmlFor="start-date">Date treatment started</label>
            <input
              id="start-date"
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
            />
            <FieldError error={update.error} field="art_start_date" />
          </div>
          <div className="field">
            <label htmlFor="regimen">Regimen (optional)</label>
            <input
              id="regimen"
              value={regimen}
              onChange={(event) => setRegimen(event.target.value)}
            />
          </div>
        </>
      )}
      {needsNote && (
        <div className="field">
          <label htmlFor="barrier-note">
            Why linkage has not happened. Read at the monthly review.
          </label>
          <textarea
            id="barrier-note"
            rows={3}
            style={{ width: "100%" }}
            value={barrierNote}
            onChange={(event) => setBarrierNote(event.target.value)}
          />
          <FieldError error={update.error} field="barrier_note" />
        </div>
      )}
      <div className="actions">
        <button className="secondary" onClick={onClose}>Cancel</button>
        <button
          disabled={
            update.isPending ||
            (needsDate && !startDate) ||
            (needsNote && !barrierNote.trim())
          }
          onClick={() =>
            update.mutate(
              {
                id: linkage.id,
                status: status as ArtLinkage["status"],
                ...(needsDate ? { art_start_date: startDate, regimen } : {}),
                ...(needsNote ? { barrier_note: barrierNote.trim() } : {}),
              },
              { onSuccess: onClose },
            )
          }
        >
          Save
        </button>
      </div>
    </Modal>
  );
}

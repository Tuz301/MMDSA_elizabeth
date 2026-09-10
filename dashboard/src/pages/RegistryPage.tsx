/**
 * The registry: clients and infants, with enrolment.
 *
 * Names render through displayName, which falls back to the programme code
 * when the server has pruned the identifier for this role — so the code-only
 * view an LGA coordinator sees is a designed state of the same screen.
 *
 * Enrolment enforces what the server enforces, stated up front: no consent,
 * no digital record. Registering an infant creates the full testing schedule
 * in the same request, and the form says so.
 */

import { useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";

import {
  useClients,
  useEnrolClient,
  useInfants,
  useMentorMothers,
  useRegisterInfant,
} from "../api/hooks";
import type { Client, Me } from "../api/types";
import { displayName, formatDate } from "../lib/format";
import { Empty, ErrorBanner, FieldError, Modal, StatusPill } from "../components/ui";

export function RegistryPage() {
  const me = useOutletContext<Me>();
  const [tab, setTab] = useState<"clients" | "infants">("clients");
  const [enrolling, setEnrolling] = useState(false);
  const [registering, setRegistering] = useState<Client | null>(null);

  return (
    <>
      <h1>Registry</h1>
      <p className="subtitle">
        {me.may_read_identifiers
          ? "Your role may read names and contact details. Every read is recorded in the audit trail."
          : "Your role sees programme codes only. That is by design, not an error."}
      </p>

      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "clients"}
          className={tab === "clients" ? "active" : ""}
          onClick={() => setTab("clients")}
        >
          Clients
        </button>
        <button
          role="tab"
          aria-selected={tab === "infants"}
          className={tab === "infants" ? "active" : ""}
          onClick={() => setTab("infants")}
        >
          Infants
        </button>
      </div>

      {tab === "clients" && (
        <ClientsTab onEnrol={() => setEnrolling(true)} onRegisterInfant={setRegistering} />
      )}
      {tab === "infants" && <InfantsTab />}

      {enrolling && <EnrolClientModal me={me} onClose={() => setEnrolling(false)} />}
      {registering && (
        <RegisterInfantModal
          mother={registering}
          me={me}
          onClose={() => setRegistering(null)}
        />
      )}
    </>
  );
}

function ClientsTab({
  onEnrol,
  onRegisterInfant,
}: {
  onEnrol: () => void;
  onRegisterInfant: (client: Client) => void;
}) {
  const clients = useClients({ status: "ACTIVE" });
  const rows = clients.data?.results ?? [];

  return (
    <>
      <ErrorBanner error={clients.error} />
      <div className="toolbar">
        <button onClick={onEnrol}>Enrol a client…</button>
      </div>
      {rows.length === 0 ? (
        <Empty>No active clients in your scope.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Client</th>
                <th>Code</th>
                <th>Stage</th>
                <th>Expected delivery</th>
                <th>Enrolled</th>
                <th>SMS consent</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((client) => (
                <tr key={client.id}>
                  <td>{displayName(client, client.client_code)}</td>
                  <td className="code">{client.client_code}</td>
                  <td>{client.pregnancy_stage}</td>
                  <td>{formatDate(client.expected_delivery_date)}</td>
                  <td>{formatDate(client.date_enrolled)}</td>
                  <td>{client.sms_contact_permitted ? "yes" : "no"}</td>
                  <td>
                    <button className="subtle" onClick={() => onRegisterInfant(client)}>
                      Register infant…
                    </button>
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

function InfantsTab() {
  const [awaitingOnly, setAwaitingOnly] = useState(false);
  const infants = useInfants(awaitingOnly ? { awaiting_art_linkage: "true" } : {});
  const rows = infants.data?.results ?? [];

  return (
    <>
      <ErrorBanner error={infants.error} />
      <div className="toolbar">
        <label style={{ margin: 0 }}>
          <input
            type="checkbox"
            checked={awaitingOnly}
            onChange={(event) => setAwaitingOnly(event.target.checked)}
          />{" "}
          Awaiting treatment linkage only
        </label>
      </div>
      {rows.length === 0 ? (
        <Empty>
          {awaitingOnly ? "No infant is awaiting linkage." : "No infants in your scope."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Baby code</th>
                <th>Mother</th>
                <th>Born</th>
                <th>Age</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((infant) => (
                <tr key={infant.id} className={infant.is_awaiting_art_linkage ? "row-urgent" : ""}>
                  <td className="code">{infant.baby_code}</td>
                  <td className="code">{infant.mother}</td>
                  <td>{formatDate(infant.date_of_birth)}</td>
                  <td>{infant.age_in_weeks} wk</td>
                  <td><StatusPill value={infant.outcome} label={infant.outcome.replaceAll("_", " ")} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function EnrolClientModal({ me, onClose }: { me: Me; onClose: () => void }) {
  const enrol = useEnrolClient();
  const mentorMothers = useMentorMothers();
  const [form, setForm] = useState({
    client_code: "",
    full_name: "",
    phone_number: "",
    mentor_mother: "",
    pregnancy_stage: "ANC",
    expected_delivery_date: "",
    consent_date: "",
    consent_form_reference: "",
    sms_contact_permitted: false,
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    enrol.mutate(
      {
        facility: me.facility?.id,
        client_code: form.client_code.trim(),
        full_name: form.full_name.trim(),
        phone_number: form.phone_number.trim() || undefined,
        mentor_mother: form.mentor_mother || null,
        pregnancy_stage: form.pregnancy_stage,
        expected_delivery_date: form.expected_delivery_date || null,
        consent_given_at: form.consent_date
          ? new Date(`${form.consent_date}T12:00:00`).toISOString()
          : null,
        consent_form_reference: form.consent_form_reference.trim(),
        sms_contact_permitted: form.sms_contact_permitted,
      },
      { onSuccess: onClose },
    );
  }

  return (
    <Modal title="Enrol a client" onClose={onClose}>
      <p style={{ marginTop: 0, color: "var(--ink-soft)" }}>
        No consent, no digital record: the signed form comes first, and its
        reference is required.
      </p>
      <ErrorBanner error={enrol.error} />
      <form onSubmit={submit}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="client-code">Client code</label>
            <input
              id="client-code"
              required
              value={form.client_code}
              onChange={(event) => set("client_code", event.target.value)}
            />
            <FieldError error={enrol.error} field="client_code" />
          </div>
          <div className="field">
            <label htmlFor="full-name">Full name</label>
            <input
              id="full-name"
              required
              value={form.full_name}
              onChange={(event) => set("full_name", event.target.value)}
            />
            <FieldError error={enrol.error} field="full_name" />
          </div>
          <div className="field">
            <label htmlFor="phone">Telephone (optional, +234…)</label>
            <input
              id="phone"
              value={form.phone_number}
              onChange={(event) => set("phone_number", event.target.value)}
            />
            <FieldError error={enrol.error} field="phone_number" />
          </div>
          <div className="field">
            <label htmlFor="mentor-mother">Mentor mother</label>
            <select
              id="mentor-mother"
              value={form.mentor_mother}
              onChange={(event) => set("mentor_mother", event.target.value)}
            >
              <option value="">Not assigned yet</option>
              {(mentorMothers.data?.results ?? []).map((mm) => (
                <option key={mm.id} value={mm.id}>
                  {displayName(mm, mm.staff_code)} ({mm.staff_code})
                </option>
              ))}
            </select>
            <FieldError error={enrol.error} field="mentor_mother" />
          </div>
          <div className="field">
            <label htmlFor="stage">Pregnancy stage</label>
            <select
              id="stage"
              value={form.pregnancy_stage}
              onChange={(event) => set("pregnancy_stage", event.target.value)}
            >
              <option value="ANC">Antenatal</option>
              <option value="LABOUR">In labour or delivery</option>
              <option value="PNC">Postnatal</option>
              <option value="NONE">Not currently pregnant</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="edd">Expected delivery (optional)</label>
            <input
              id="edd"
              type="date"
              value={form.expected_delivery_date}
              onChange={(event) => set("expected_delivery_date", event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="consent-date">Consent form signed on</label>
            <input
              id="consent-date"
              type="date"
              required
              value={form.consent_date}
              onChange={(event) => set("consent_date", event.target.value)}
            />
            <FieldError error={enrol.error} field="consent_given_at" />
          </div>
          <div className="field">
            <label htmlFor="consent-ref">Consent form reference</label>
            <input
              id="consent-ref"
              required
              value={form.consent_form_reference}
              onChange={(event) => set("consent_form_reference", event.target.value)}
            />
            <FieldError error={enrol.error} field="consent_form_reference" />
          </div>
        </div>
        <div className="field">
          <label style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={form.sms_contact_permitted}
              onChange={(event) => set("sms_contact_permitted", event.target.checked)}
            />{" "}
            The client agreed to receive SMS. Enrolment consent is not SMS consent.
          </label>
        </div>
        <div className="actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button disabled={enrol.isPending}>Enrol</button>
        </div>
      </form>
    </Modal>
  );
}

function RegisterInfantModal({
  mother,
  me,
  onClose,
}: {
  mother: Client;
  me: Me;
  onClose: () => void;
}) {
  const register = useRegisterInfant();
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [sex, setSex] = useState("F");
  const [givenName, setGivenName] = useState("");

  return (
    <Modal
      title={`Register an infant of ${displayName(mother, mother.client_code)}`}
      onClose={onClose}
    >
      <p style={{ marginTop: 0, color: "var(--ink-soft)" }}>
        Registration creates the full testing schedule — birth, 6 weeks, 9
        months, 18 months — in the same step, and issues the Baby Code used in
        every SMS.
      </p>
      <ErrorBanner error={register.error} />
      <div className="field">
        <label htmlFor="dob">Date of birth</label>
        <input
          id="dob"
          type="date"
          value={dateOfBirth}
          onChange={(event) => setDateOfBirth(event.target.value)}
        />
        <FieldError error={register.error} field="date_of_birth" />
      </div>
      <div className="field">
        <label htmlFor="sex">Sex</label>
        <select id="sex" value={sex} onChange={(event) => setSex(event.target.value)}>
          <option value="F">Female</option>
          <option value="M">Male</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="given-name">Given name (optional)</label>
        <input
          id="given-name"
          value={givenName}
          onChange={(event) => setGivenName(event.target.value)}
        />
      </div>
      <div className="actions">
        <button className="secondary" onClick={onClose}>Cancel</button>
        <button
          disabled={!dateOfBirth || register.isPending}
          onClick={() =>
            register.mutate(
              {
                mother: mother.client_code,
                facility: me.facility?.id ?? mother.facility,
                date_of_birth: dateOfBirth,
                sex,
                given_name: givenName.trim() || undefined,
              },
              { onSuccess: onClose },
            )
          }
        >
          Register
        </button>
      </div>
    </Modal>
  );
}

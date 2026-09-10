/**
 * Types for the MMDSA API.
 *
 * Written by hand rather than generated, for one reason that matters: the
 * server removes identifier fields (names, telephone numbers, addresses,
 * household coordinates, free-text notes) from responses for roles outside
 * the identifier-read set. A generated type would mark those fields as always
 * present and every screen would quietly assume them. Here they are optional,
 * so the compiler forces every screen to handle the code-only view a
 * coordinator or state manager receives.
 */

export type Role =
  | "MENTOR_MOTHER"
  | "FACILITY_SUPERVISOR"
  | "LGA_COORDINATOR"
  | "STATE_MANAGER"
  | "SYSTEM_ADMIN";

export interface ScopeReference {
  id: string;
  name: string;
  code: string;
}

export interface Me {
  id: string;
  username: string;
  email: string;
  role: Role;
  role_display: string;
  facility: ScopeReference | null;
  lga: ScopeReference | null;
  state: ScopeReference | null;
  may_enter_clinical_data: boolean;
  may_read_identifiers: boolean;
  may_administer_users: boolean;
  mentor_mother_staff_code: string | null;
  must_change_password: boolean;
  last_active_at: string | null;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// -- Registry ---------------------------------------------------------------

export interface Facility {
  id: string;
  lga: string;
  name: string;
  code: string;
  level: "PRIMARY" | "SECONDARY" | "TERTIARY";
  is_pilot_site: boolean;
  is_ready_for_activation: boolean;
}

export interface Client {
  id: string;
  facility: string;
  mentor_mother: string | null;
  client_code: string;
  status: "ACTIVE" | "TRANSFERRED" | "LTFU" | "EXITED" | "DECEASED";
  pregnancy_stage: "ANC" | "LABOUR" | "PNC" | "NONE";
  expected_delivery_date: string | null;
  date_enrolled: string;
  consent_given_at: string | null;
  consent_form_reference?: string;
  sms_contact_permitted: boolean;
  has_valid_consent: boolean;
  /** Identifier fields: absent for roles without identifier read. */
  full_name?: string;
  phone_number?: string;
  alternate_phone_number?: string;
  household_address?: string;
  hospital_number?: string;
  household_latitude?: string | null;
  household_longitude?: string | null;
  year_of_birth: number | null;
}

export type InfantOutcome =
  | "IN_FOLLOW_UP"
  | "NEG_DISCHARGED"
  | "POS_ON_ART"
  | "POS_NOT_LINKED"
  | "LTFU"
  | "TRANSFERRED"
  | "DECEASED";

export interface Infant {
  id: string;
  mother: string; // client_code
  facility: string;
  baby_code: string;
  date_of_birth: string;
  sex: "F" | "M";
  birth_weight_grams: number | null;
  outcome: InfantOutcome;
  outcome_recorded_at: string | null;
  age_in_weeks: number;
  is_awaiting_art_linkage: boolean;
  /** Identifier field: absent for roles without identifier read. */
  given_name?: string;
}

export interface MentorMother {
  id: string;
  facility: string;
  staff_code: string;
  status: "ACTIVE" | "ON_LEAVE" | "EXITED";
  has_smartphone: boolean;
  is_training_passed: boolean;
  /** Identifier fields: absent for roles without identifier read. */
  full_name?: string;
  phone_number?: string;
}

// -- EID --------------------------------------------------------------------

export type Milestone = "BIRTH" | "WEEK_6" | "MONTH_9" | "MONTH_18" | "UNSCHEDULED";

export interface EidAppointment {
  id: string;
  infant: string; // baby_code
  milestone: Milestone;
  due_date: string;
  status: "SCHEDULED" | "ATTENDED" | "MISSED" | "RESCHEDULED" | "CANCELLED";
  attended_date: string | null;
  days_overdue: number;
}

export type SampleResult =
  | "NEGATIVE"
  | "POSITIVE"
  | "INDETERMINATE"
  | "REJECTED"
  | "PENDING";

export interface EidSample {
  id: string;
  appointment: string;
  baby_code: string;
  sample_identifier: string;
  collected_on: string;
  dispatched_on: string | null;
  laboratory_received_on: string | null;
  laboratory_name: string;
  result: SampleResult;
  result_issued_on: string | null;
  result_entered_at: string | null;
  result_acknowledged_at: string | null;
  caregiver_informed_at: string | null;
  entered_by: string | null;
  acknowledged_by: string | null;
  days_collection_to_result: number | null;
  hours_entry_to_acknowledgement: number | null;
  is_unacknowledged_positive: boolean;
}

export type LinkageStatus =
  | "PENDING"
  | "STARTED"
  | "REFUSED"
  | "UNREACHABLE"
  | "TRANSFERRED"
  | "DECEASED";

export interface ArtLinkage {
  id: string;
  infant: string; // baby_code
  triggering_sample: string;
  status: LinkageStatus;
  art_start_date: string | null;
  regimen: string;
  treating_facility: string | null;
  barrier_note: string;
  recorded_by: string | null;
  days_to_linkage: number | null;
  days_outstanding: number | null;
}

// -- Alerts -----------------------------------------------------------------

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface Alert {
  id: string;
  alert_type: string;
  severity: Severity;
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED" | "ESCALATED" | "CANCELLED";
  facility: string;
  assigned_mentor_mother: string | null;
  subject_type: string;
  subject_id: string;
  title: string;
  detail: string;
  raised_at: string;
  acknowledge_by: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  acknowledgement_channel: "" | "APP" | "SMS";
  resolved_at: string | null;
  resolution_note: string;
  escalation_level: number;
  is_past_deadline: boolean;
  hours_to_acknowledgement: number | null;
}

// -- Visits -----------------------------------------------------------------

export type LocationStatus =
  | "VERIFIED"
  | "OUT_OF_RANGE"
  | "LOW_ACCURACY"
  | "NO_FIX"
  | "NO_REFERENCE";

export interface HomeVisit {
  id: string;
  client: string; // client_code
  infant: string | null; // baby_code
  mentor_mother: string; // staff_code
  purpose: string;
  result: string;
  visit_date: string;
  location_status: LocationStatus;
  distance_from_household_metres: number | null;
  flagged_for_review: boolean;
  review_reason: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_outcome: string;
  /** Identifier fields: absent for roles without identifier read. */
  latitude?: string | null;
  longitude?: string | null;
  notes?: string;
}

export interface GeospatialAnomaly {
  id: string;
  mentor_mother: string; // staff_code
  kind:
    | "IMPOSSIBLE_TRAVEL"
    | "IDENTICAL_POSITION"
    | "CLUSTERED_DAY"
    | "BULK_BACKDATED";
  detected_for_date: string;
  detail: string;
  confidence: "LOW" | "MEDIUM" | "HIGH";
  disposition: "OPEN" | "EXPLAINED" | "DATA_CORRECTED" | "ESCALATED";
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string;
}

export interface SyncBatch {
  id: string;
  device_id: string;
  user: string;
  status: "ACCEPTED" | "PARTIAL" | "REJECTED";
  records_submitted: number;
  records_accepted: number;
  records_rejected: number;
  oldest_record_created_at: string | null;
  backlog_hours: number | null;
  created_at: string;
}

// -- Metrics ----------------------------------------------------------------

export interface MetricsSummary {
  window: { date_from: string; date_to: string };
  eid_cascade: {
    unacknowledged_positives_now: number;
    relay_delay_median_hours_all: number | null;
    relay_delay_median_hours_positive: number | null;
  };
  art_linkage: {
    days_to_linkage_median: number | null;
    linkage_target_days: number;
    pct_linked_within_target: number | null;
    cohort_size: number;
    linkages_missing_issue_date: number;
  };
  alerts: {
    raised: number;
    sla_compliance_pct: number | null;
    past_deadline_now: number;
  };
  location_verification: {
    visits: number;
    verified_pct: number | null;
    target_pct: number;
    verdicts: Partial<Record<LocationStatus, number>>;
  };
  sms: {
    dispatch_attempted: number;
    delivery_failure_pct: number | null;
    blocked_by_privacy_guard: number;
  };
}

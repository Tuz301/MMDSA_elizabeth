/**
 * Query and mutation hooks, one per API operation the dashboard uses.
 *
 * Two conventions run through this module. Reads poll: the dashboard's whole
 * purpose is that an unacknowledged positive is seen within minutes, so the
 * worklists refresh themselves rather than waiting for a reload. Writes
 * invalidate broadly: a result entry moves an infant, a linkage, an alert
 * and the summary at once, and a stale panel that says the work is still
 * outstanding trains supervisors to distrust the screen.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, query } from "./client";
import type {
  Alert,
  ArtLinkage,
  Client,
  EidAppointment,
  EidSample,
  Facility,
  HomeVisit,
  Infant,
  Me,
  MentorMother,
  MetricsSummary,
  Paginated,
  SyncBatch,
} from "./types";

const WORKLIST_REFRESH_MS = 60_000;

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/v1/accounts/me/"),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useMetricsSummary() {
  return useQuery({
    queryKey: ["metrics-summary"],
    queryFn: () => api.get<MetricsSummary>("/api/v1/metrics/summary/"),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

// -- Alerts -----------------------------------------------------------------

export function useAlerts(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["alerts", filters],
    queryFn: () =>
      api.get<Paginated<Alert>>(`/api/v1/alerts/${query({ limit: 100, ...filters })}`),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

function useInvalidator(keys: string[][]) {
  const client = useQueryClient();
  return () => keys.forEach((key) => void client.invalidateQueries({ queryKey: key }));
}

export function useAcknowledgeAlert() {
  const invalidate = useInvalidator([["alerts"], ["metrics-summary"]]);
  return useMutation({
    mutationFn: (id: string) => api.post<Alert>(`/api/v1/alerts/${id}/acknowledge/`),
    onSuccess: invalidate,
  });
}

export function useResolveAlert() {
  const invalidate = useInvalidator([["alerts"], ["metrics-summary"]]);
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      api.post<Alert>(`/api/v1/alerts/${id}/resolve/`, { note }),
    onSuccess: invalidate,
  });
}

// -- EID --------------------------------------------------------------------

const EID_KEYS = [["samples"], ["appointments"], ["linkages"], ["infants"], ["alerts"], ["metrics-summary"]];

export function useSamples(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["samples", filters],
    queryFn: () =>
      api.get<Paginated<EidSample>>(`/api/v1/eid/samples/${query({ limit: 100, ...filters })}`),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

export function useAppointments(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["appointments", filters],
    queryFn: () =>
      api.get<Paginated<EidAppointment>>(
        `/api/v1/eid/appointments/${query({ limit: 100, ...filters })}`,
      ),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

export function useLinkages(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["linkages", filters],
    queryFn: () =>
      api.get<Paginated<ArtLinkage>>(`/api/v1/eid/linkages/${query({ limit: 100, ...filters })}`),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

export function useRegisterSample() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: (body: {
      appointment: string;
      sample_identifier: string;
      collected_on: string;
      dispatched_on?: string;
      laboratory_name?: string;
    }) => api.post<EidSample>("/api/v1/eid/samples/", body),
    onSuccess: invalidate,
  });
}

export function useEnterResult() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      result: string;
      result_issued_on: string;
      laboratory_name?: string;
    }) => api.post<EidSample>(`/api/v1/eid/samples/${id}/result/`, body),
    onSuccess: invalidate,
  });
}

export function useAcknowledgeSample() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: (id: string) => api.post<EidSample>(`/api/v1/eid/samples/${id}/acknowledge/`),
    onSuccess: invalidate,
  });
}

export function useCaregiverInformed() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: (id: string) =>
      api.post<EidSample>(`/api/v1/eid/samples/${id}/caregiver-informed/`),
    onSuccess: invalidate,
  });
}

export function useUpdateLinkage() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Partial<ArtLinkage>) =>
      api.patch<ArtLinkage>(`/api/v1/eid/linkages/${id}/`, body),
    onSuccess: invalidate,
  });
}

export function useAppointmentAction() {
  const invalidate = useInvalidator(EID_KEYS);
  return useMutation({
    mutationFn: ({
      id,
      action,
      body,
    }: {
      id: string;
      action: "attended" | "missed" | "reschedule";
      body?: Record<string, string>;
    }) => api.post<EidAppointment>(`/api/v1/eid/appointments/${id}/${action}/`, body),
    onSuccess: invalidate,
  });
}

// -- Registry ---------------------------------------------------------------

export function useClients(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["clients", filters],
    queryFn: () =>
      api.get<Paginated<Client>>(
        `/api/v1/registry/clients/${query({ limit: 100, ...filters })}`,
      ),
  });
}

export function useInfants(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["infants", filters],
    queryFn: () =>
      api.get<Paginated<Infant>>(
        `/api/v1/registry/infants/${query({ limit: 100, ...filters })}`,
      ),
  });
}

export function useMentorMothers() {
  return useQuery({
    queryKey: ["mentor-mothers"],
    queryFn: () =>
      api.get<Paginated<MentorMother>>("/api/v1/registry/mentor-mothers/?limit=200"),
    staleTime: 5 * 60_000,
  });
}

export function useFacilities() {
  return useQuery({
    queryKey: ["facilities"],
    queryFn: () => api.get<Paginated<Facility>>("/api/v1/registry/facilities/?limit=200"),
    staleTime: 5 * 60_000,
  });
}

export function useEnrolClient() {
  const invalidate = useInvalidator([["clients"]]);
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<Client>("/api/v1/registry/clients/", body),
    onSuccess: invalidate,
  });
}

export function useRegisterInfant() {
  const invalidate = useInvalidator([["infants"], ["appointments"]]);
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<Infant>("/api/v1/registry/infants/", body),
    onSuccess: invalidate,
  });
}

// -- Visits -----------------------------------------------------------------

export function useVisits(filters: Record<string, string | undefined>) {
  return useQuery({
    queryKey: ["visits", filters],
    queryFn: () =>
      api.get<Paginated<HomeVisit>>(
        `/api/v1/visits/records/${query({ limit: 100, ...filters })}`,
      ),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

export function useReviewVisit() {
  const invalidate = useInvalidator([["visits"], ["alerts"], ["metrics-summary"]]);
  return useMutation({
    mutationFn: ({ id, review_outcome }: { id: string; review_outcome: string }) =>
      api.post<HomeVisit>(`/api/v1/visits/records/${id}/review/`, { review_outcome }),
    onSuccess: invalidate,
  });
}

export function useSyncBatches() {
  return useQuery({
    queryKey: ["sync-batches"],
    queryFn: () => api.get<Paginated<SyncBatch>>("/api/v1/visits/sync-batches/?limit=50"),
    refetchInterval: WORKLIST_REFRESH_MS,
  });
}

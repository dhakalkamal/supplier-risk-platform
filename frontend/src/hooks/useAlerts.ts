import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { DataResponse, ListResponse, Alert, AlertStatus } from "@/types/api";

export interface AlertFilters {
  status?: AlertStatus | "all";
  severity?: "low" | "medium" | "high" | "critical";
  supplier_id?: string;
  alert_type?: string;
  page?: number;
  per_page?: number;
}

export function useAlerts(filters: AlertFilters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined) params.set(k, String(v));
  });
  return useQuery({
    queryKey: ["alerts", filters],
    queryFn: () =>
      apiFetch<ListResponse<Alert>>(`/api/v1/alerts?${params.toString()}`),
  });
}

interface PatchAlertPayload {
  alertId: string;
  status?: AlertStatus;
  note?: string;
}

export function usePatchAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ alertId, status, note }: PatchAlertPayload) =>
      apiFetch<DataResponse<{ alert_id: string; status: AlertStatus; note: string | null; updated_at: string }>>(
        `/api/v1/alerts/${alertId}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status, note }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      void queryClient.invalidateQueries({ queryKey: ["portfolio", "summary"] });
    },
  });
}

// Valid status transitions per API spec
export const VALID_NEXT_STATUSES: Record<AlertStatus, AlertStatus[]> = {
  new: ["investigating", "resolved", "dismissed"],
  investigating: ["resolved", "new"],
  resolved: ["investigating"],
  dismissed: ["investigating"],
};

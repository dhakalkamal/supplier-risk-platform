import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { DataResponse, ListResponse, AlertRules, TenantUser, PendingInvite } from "@/types/api";

export function useAlertRules() {
  return useQuery({
    queryKey: ["settings", "alert-rules"],
    queryFn: () =>
      apiFetch<DataResponse<AlertRules>>("/api/v1/settings/alert-rules").then(
        (r) => r.data,
      ),
  });
}

export function useUpdateAlertRules() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rules: Partial<AlertRules>) =>
      apiFetch<DataResponse<AlertRules>>("/api/v1/settings/alert-rules", {
        method: "PUT",
        body: JSON.stringify(rules),
      }).then((r) => r.data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "alert-rules"] });
    },
  });
}

export function useUsers() {
  return useQuery({
    queryKey: ["settings", "users"],
    queryFn: () =>
      apiFetch<ListResponse<TenantUser>>("/api/v1/settings/users").then(
        (r) => r.data,
      ),
  });
}

export function usePendingInvites() {
  return useQuery({
    queryKey: ["settings", "invites"],
    queryFn: () =>
      apiFetch<ListResponse<PendingInvite>>("/api/v1/settings/users/invites").then(
        (r) => r.data,
      ),
  });
}

export function useInviteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { email: string; role: "admin" | "viewer" }) =>
      apiFetch<DataResponse<PendingInvite>>("/api/v1/settings/users/invite", {
        method: "POST",
        body: JSON.stringify(payload),
      }).then((r) => r.data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}

export function useRemoveUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      apiFetch(`/api/v1/settings/users/${userId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "users"] });
    },
  });
}

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  DataResponse,
  ListResponse,
  PortfolioSummary,
  SupplierSummary,
} from "@/types/api";

export interface SuppliersParams {
  page?: number;
  per_page?: number;
  sort_by?: "risk_score" | "name" | "last_updated" | "date_added";
  sort_order?: "asc" | "desc";
  risk_level?: "high" | "medium" | "low";
  country?: string;
  search?: string;
  tag?: string;
}

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ["portfolio", "summary"],
    queryFn: () =>
      apiFetch<DataResponse<PortfolioSummary>>("/api/v1/portfolio/summary").then(
        (r) => r.data,
      ),
    staleTime: 5 * 60 * 1000,
  });
}

export function usePortfolioSuppliers(params: SuppliersParams = {}) {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") searchParams.set(k, String(v));
  });
  return useQuery({
    queryKey: ["portfolio", "suppliers", params],
    queryFn: () =>
      apiFetch<ListResponse<SupplierSummary>>(
        `/api/v1/portfolio/suppliers?${searchParams}`,
      ),
  });
}

export function useRemoveSupplier() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (portfolioSupplierId: string) =>
      apiFetch(`/api/v1/portfolio/suppliers/${portfolioSupplierId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });
}

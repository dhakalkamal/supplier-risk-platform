import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  DataResponse,
  ListResponse,
  SupplierProfile,
  ScoreHistory,
  NewsArticle,
} from "@/types/api";

export function useSupplier(supplierId: string) {
  return useQuery({
    queryKey: ["supplier", supplierId],
    queryFn: () =>
      apiFetch<DataResponse<SupplierProfile>>(
        `/api/v1/suppliers/${supplierId}`,
      ).then((r) => r.data),
    enabled: !!supplierId,
    staleTime: 60 * 60 * 1000,
  });
}

export function useScoreHistory(supplierId: string, days: number) {
  return useQuery({
    queryKey: ["supplier", supplierId, "score-history", days],
    queryFn: () =>
      apiFetch<DataResponse<ScoreHistory>>(
        `/api/v1/suppliers/${supplierId}/score-history?days=${days}`,
      ).then((r) => r.data),
    enabled: !!supplierId,
    staleTime: 6 * 60 * 60 * 1000,
  });
}

export interface NewsFilters {
  page?: number;
  per_page?: number;
  sentiment?: "positive" | "negative" | "neutral";
  days?: number;
}

export function useSupplierNews(supplierId: string, filters: NewsFilters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined) params.set(k, String(v));
  });
  return useQuery({
    queryKey: ["supplier", supplierId, "news", filters],
    queryFn: () =>
      apiFetch<ListResponse<NewsArticle>>(
        `/api/v1/suppliers/${supplierId}/news?${params.toString()}`,
      ),
    enabled: !!supplierId,
  });
}

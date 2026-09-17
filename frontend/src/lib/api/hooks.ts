import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export const useUser = () => useQuery({ queryKey: ["user"], queryFn: api.getMe });
export const useSubscription = () => useQuery({ queryKey: ["subscription"], queryFn: api.getSubscription });
export const useDashboardSummary = () => useQuery({ queryKey: ["dashboardSummary"], queryFn: api.getDashboardSummary });
export const useQueries = () => useQuery({ queryKey: ["queries"], queryFn: api.getQueries });

export const useCreateQuery = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createQuery,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queries"] });
      qc.invalidateQueries({ queryKey: ["dashboardSummary"] });
    },
  });
};

export const useUpdateQuery = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: any }) => api.updateQuery(id, updates),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["queries"] }),
  });
};

export const useDeleteQuery = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteQuery,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queries"] });
      qc.invalidateQueries({ queryKey: ["dashboardSummary"] });
    },
  });
};

export const useToggleQueryStatus = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, currentStatus }: { id: string; currentStatus: "active" | "paused" }) => 
      currentStatus === "active" ? api.pauseQuery(id) : api.resumeQuery(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queries"] });
      qc.invalidateQueries({ queryKey: ["dashboardSummary"] });
    },
  });
};

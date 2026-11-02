"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { API, CONFIG, QUERY_KEYS, UI } from "@/constants";
import type { EarningsOverview, WithdrawMethod } from "../types";

export function useEarningsOverview(enabled = true) {
  return useQuery<EarningsOverview>({
    queryKey: QUERY_KEYS.billing.earnings(),
    queryFn: async () => {
      const res = await fetch(API.BILLING.CONNECT_WITHDRAW);
      if (!res.ok) throw new Error(UI.CONNECT.WITHDRAW_ERROR);
      return res.json() as Promise<EarningsOverview>;
    },
    staleTime: CONFIG.STALE_TIME_MS,
    retry: false,
    enabled,
  });
}

export function useConnectWithdraw() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { amount: string; method: WithdrawMethod }) => {
      const res = await fetch(API.BILLING.CONNECT_WITHDRAW, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error || UI.CONNECT.WITHDRAW_ERROR);
      }
      return res.json() as Promise<{ transferId: string; intentId: string }>;
    },
    onSuccess: async () => {
      toast.success(UI.CONNECT.WITHDRAW_SUCCESS);
      await queryClient.invalidateQueries({ queryKey: QUERY_KEYS.billing.earnings() });
    },
    onError: (error: Error) => toast.error(error.message || UI.CONNECT.WITHDRAW_ERROR),
  });
}

import { BILLING } from "@/constants";
import type { WithdrawMethod } from "../types";

/** Country / entity limits for Connect Express + stablecoin preview (#111). */
export const CONNECT_OFFRAMP_LIMITS = {
  platform: "US-based Connect platforms only (Stripe private preview for stablecoin).",
  recipients:
    "Individuals / sole proprietors on Stripe's Connect recipient list. US individuals in NY and HI are excluded from stablecoin payouts.",
  entities: "Companies and non-profits are not supported for Connect stablecoin payouts yet.",
  defaultRail: "On-chain pay-per-fragment remains the default settlement path.",
} as const;

export function stablecoinWithdrawEnabled(
  flag: string | undefined = process.env.STRIPE_CONNECT_STABLECOIN_PAYOUTS,
): boolean {
  return (flag ?? "").trim().toLowerCase() === "true";
}

export function parseWithdrawAmount(raw: string): string {
  const trimmed = raw.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) {
    throw new Error("Enter an amount with up to two decimal places");
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error("Amount must be greater than zero");
  }
  return trimmed.includes(".") ? trimmed : `${trimmed}.00`;
}

export function withdrawMethodLabel(method: WithdrawMethod): string {
  return method === BILLING.CONNECT_WITHDRAW_STABLECOIN
    ? "Stablecoin (USDC via Express)"
    : "Bank payout";
}

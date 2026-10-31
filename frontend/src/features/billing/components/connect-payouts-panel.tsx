"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BILLING, UI } from "@/constants";
import { connectStatusLabel } from "../lib/connect";
import type {
  ConnectOverview,
  ConnectPayoutStatus,
  EarningsOverview,
  WithdrawMethod,
} from "../types";
import {
  useConnectDashboard,
  useConnectOnboard,
  useConnectOverview,
  useConnectReturn,
} from "../hooks/use-connect";
import { useConnectWithdraw, useEarningsOverview } from "../hooks/use-withdraw";

function statusVariant(
  status: ConnectPayoutStatus,
): "default" | "secondary" | "outline" {
  if (status === BILLING.CONNECT_STATUS_READY) return "default";
  if (status === BILLING.CONNECT_STATUS_PENDING) return "outline";
  return "secondary";
}

/** Presentational card used by the settings panel and screenshot fixtures. */
export function ConnectPayoutsCard({
  overview,
  onboarding,
  openingDashboard,
  onOnboard,
  onDashboard,
}: {
  overview: ConnectOverview;
  onboarding?: boolean;
  openingDashboard?: boolean;
  onOnboard?: () => void;
  onDashboard?: () => void;
}) {
  const { status } = overview;
  const showOnboard =
    status === BILLING.CONNECT_STATUS_NOT_STARTED ||
    status === BILLING.CONNECT_STATUS_PENDING;
  const showDashboard =
    overview.dashboardAvailable && status !== BILLING.CONNECT_STATUS_UNAVAILABLE;

  return (
    <Card className="border-hairline">
      <CardHeader>
        <CardTitle className="text-base font-medium">{UI.CONNECT.TITLE}</CardTitle>
        <CardDescription>{UI.CONNECT.DESCRIPTION}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <Badge variant={statusVariant(status)}>{connectStatusLabel(status)}</Badge>
          <div className="flex flex-wrap gap-2">
            {showOnboard ? (
              <Button
                variant="outline"
                disabled={onboarding || status === BILLING.CONNECT_STATUS_UNAVAILABLE}
                onClick={onOnboard}
              >
                {status === BILLING.CONNECT_STATUS_PENDING
                  ? UI.CONNECT.CONTINUE_CTA
                  : UI.CONNECT.ONBOARD_CTA}
              </Button>
            ) : null}
            {showDashboard ? (
              <Button
                variant="outline"
                disabled={openingDashboard}
                onClick={onDashboard}
              >
                {UI.CONNECT.DASHBOARD_CTA}
              </Button>
            ) : null}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{UI.CONNECT.SKIP_NOTE}</p>
        {status === BILLING.CONNECT_STATUS_UNAVAILABLE ? (
          <p className="text-xs text-muted-foreground">{UI.CONNECT.UNAVAILABLE}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function ConnectWithdrawCard({
  earnings,
  submitting,
  onWithdraw,
}: {
  earnings: EarningsOverview;
  submitting?: boolean;
  onWithdraw?: (input: { amount: string; method: WithdrawMethod }) => void;
}) {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<WithdrawMethod>(BILLING.CONNECT_WITHDRAW_BANK);
  const ready = earnings.connectStatus === BILLING.CONNECT_STATUS_READY;
  const available = Number(earnings.available);
  const canWithdraw = ready && Number.isFinite(available) && available > 0;

  return (
    <Card className="border-hairline">
      <CardHeader>
        <CardTitle className="text-base font-medium">{UI.CONNECT.WITHDRAW_TITLE}</CardTitle>
        <CardDescription>{UI.CONNECT.WITHDRAW_DESCRIPTION}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-6 text-sm">
          <div>
            <p className="text-muted-foreground">{UI.CONNECT.WITHDRAW_BALANCE}</p>
            <p className="font-medium">{earnings.available} USDC</p>
          </div>
          <div>
            <p className="text-muted-foreground">{UI.CONNECT.WITHDRAW_HELD}</p>
            <p className="font-medium">{earnings.held} USDC</p>
          </div>
        </div>

        {!ready ? (
          <p className="text-xs text-muted-foreground">{UI.CONNECT.WITHDRAW_NEED_READY}</p>
        ) : null}
        {ready && available <= 0 ? (
          <p className="text-xs text-muted-foreground">{UI.CONNECT.WITHDRAW_EMPTY}</p>
        ) : null}

        {canWithdraw ? (
          <div className="flex flex-col gap-3">
            <div className="grid gap-2">
              <Label htmlFor="withdraw-amount">{UI.CONNECT.WITHDRAW_AMOUNT_LABEL}</Label>
              <Input
                id="withdraw-amount"
                inputMode="decimal"
                placeholder="0.00"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>{UI.CONNECT.WITHDRAW_METHOD_LABEL}</Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant={method === BILLING.CONNECT_WITHDRAW_BANK ? "default" : "outline"}
                  onClick={() => setMethod(BILLING.CONNECT_WITHDRAW_BANK)}
                >
                  {UI.CONNECT.WITHDRAW_BANK}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={
                    method === BILLING.CONNECT_WITHDRAW_STABLECOIN ? "default" : "outline"
                  }
                  disabled={!earnings.stablecoinEnabled}
                  onClick={() => setMethod(BILLING.CONNECT_WITHDRAW_STABLECOIN)}
                >
                  {UI.CONNECT.WITHDRAW_STABLECOIN}
                </Button>
              </div>
              {method === BILLING.CONNECT_WITHDRAW_STABLECOIN ? (
                <p className="text-xs text-muted-foreground">
                  {UI.CONNECT.WITHDRAW_STABLECOIN_NOTE}
                </p>
              ) : null}
            </div>
            <Button
              disabled={submitting || !amount.trim()}
              onClick={() => onWithdraw?.({ amount, method })}
            >
              {UI.CONNECT.WITHDRAW_CTA}
            </Button>
          </div>
        ) : null}

        <p className="text-xs text-muted-foreground">{UI.CONNECT.WITHDRAW_LIMITS}</p>
      </CardContent>
    </Card>
  );
}

export function ConnectPayoutsPanel() {
  useConnectReturn();
  const { data, isError, isPending } = useConnectOverview();
  const onboard = useConnectOnboard();
  const dashboard = useConnectDashboard();
  const earningsQuery = useEarningsOverview(!isPending);
  const withdraw = useConnectWithdraw();

  const overview: ConnectOverview = data ?? {
    status: isPending ? BILLING.CONNECT_STATUS_NOT_STARTED : BILLING.CONNECT_STATUS_UNAVAILABLE,
    accountId: null,
    dashboardAvailable: false,
  };

  const earnings: EarningsOverview = earningsQuery.data ?? {
    available: "0",
    held: "0",
    accountId: "",
    stablecoinEnabled: false,
    connectStatus: overview.status,
    connectedAccountId: overview.accountId,
  };

  return (
    <div className="flex flex-col gap-4">
      <ConnectPayoutsCard
        overview={overview}
        onboarding={onboard.isPending}
        openingDashboard={dashboard.isPending}
        onOnboard={() => onboard.mutate()}
        onDashboard={() => dashboard.mutate()}
      />
      <ConnectWithdrawCard
        earnings={earnings}
        submitting={withdraw.isPending}
        onWithdraw={(input) => withdraw.mutate(input)}
      />
      {isError || earningsQuery.isError ? (
        <p className="px-1 text-xs text-muted-foreground">{UI.CONNECT.ERROR}</p>
      ) : null}
    </div>
  );
}

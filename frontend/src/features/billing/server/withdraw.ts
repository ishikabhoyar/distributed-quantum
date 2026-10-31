import "server-only";

import { randomBytes } from "node:crypto";
import { BACKEND, BILLING, UI } from "@/constants";
import { backendAssertion } from "@/features/auth/server/backend-assertion";
import { getSession } from "@/features/auth/server/session";
import { getConnectConfig, getConnectOverview } from "./connect";
import { parseWithdrawAmount, stablecoinWithdrawEnabled } from "../lib/withdraw";
import type { EarningsOverview, WithdrawMethod } from "../types";

interface BillingUser {
  id: string;
  email: string;
  stripeConnectedAccountId?: string | null;
  stripeConnectPayoutsEnabled?: boolean | null;
}

interface BackendBalance {
  balance: { available: string; held: string; account_id?: string };
  account_id: string;
}

interface WithdrawIntent {
  intent_id: string;
  event_key: string;
  amount: string;
  amount_cents: number;
  method: WithdrawMethod;
  connected_account_id: string;
}

async function backendFetch(
  user: BillingUser,
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const assertion = backendAssertion(user);
  if (!assertion) throw new Error("AGENT_AUTH_BRIDGE_SECRET is not configured");
  return fetch(url, {
    ...init,
    headers: {
      Authorization: assertion,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
}

export async function getEarningsOverview(user: BillingUser): Promise<EarningsOverview> {
  const connect = await getConnectOverview(user);
  const res = await backendFetch(user, BACKEND.EARNINGS.BALANCE);
  if (!res.ok) throw new Error(`Earnings balance failed: ${res.status}`);
  const body = (await res.json()) as BackendBalance;
  return {
    available: body.balance.available,
    held: body.balance.held,
    accountId: body.account_id,
    stablecoinEnabled: stablecoinWithdrawEnabled(),
    connectStatus: connect.status,
    connectedAccountId: connect.accountId,
  };
}

export async function requestConnectWithdraw(
  user: BillingUser,
  amountRaw: string,
  method: WithdrawMethod,
): Promise<{ transferId: string; intentId: string }> {
  const config = getConnectConfig();
  if (!config) throw new Error(UI.CONNECT.UNAVAILABLE);

  const overview = await getConnectOverview(user);
  if (
    overview.status !== BILLING.CONNECT_STATUS_READY ||
    !overview.accountId
  ) {
    throw new Error(UI.CONNECT.WITHDRAW_NEED_READY);
  }
  if (method === BILLING.CONNECT_WITHDRAW_STABLECOIN && !stablecoinWithdrawEnabled()) {
    throw new Error(UI.CONNECT.WITHDRAW_STABLECOIN_NOTE);
  }

  const amount = parseWithdrawAmount(amountRaw);
  const intentId = randomBytes(12).toString("hex");

  const beginRes = await backendFetch(user, BACKEND.EARNINGS.WITHDRAW_BEGIN, {
    method: "POST",
    body: JSON.stringify({
      intent_id: intentId,
      amount,
      method,
      connected_account_id: overview.accountId,
    }),
  });
  if (!beginRes.ok) {
    const detail = await beginRes.text();
    throw new Error(detail || UI.CONNECT.WITHDRAW_ERROR);
  }
  const intent = (await beginRes.json()) as WithdrawIntent;

  try {
    const transfer = await config.stripe.transfers.create(
      {
        amount: intent.amount_cents,
        currency: BILLING.CONNECT_WITHDRAW_CURRENCY,
        destination: intent.connected_account_id,
        transfer_group: intent.event_key,
        metadata: {
          intent_id: intent.intent_id,
          method: intent.method,
          platform_user_id: user.id,
          kind: "connect_offramp",
        },
      },
      { idempotencyKey: intent.event_key },
    );

    const confirmRes = await backendFetch(user, BACKEND.EARNINGS.WITHDRAW_CONFIRM, {
      method: "POST",
      body: JSON.stringify({
        intent_id: intent.intent_id,
        transfer_id: transfer.id,
      }),
    });
    if (!confirmRes.ok) {
      throw new Error(UI.CONNECT.WITHDRAW_ERROR);
    }
    return { transferId: transfer.id, intentId: intent.intent_id };
  } catch (error) {
    const reason = error instanceof Error ? error.message : "stripe transfer failed";
    const uncertain =
      error instanceof Error &&
      (/timeout|network|ECONNRESET|fetch failed/i.test(error.message) ||
        error.name === "StripeConnectionError");

    await backendFetch(
      user,
      uncertain ? BACKEND.EARNINGS.WITHDRAW_UNCERTAIN : BACKEND.EARNINGS.WITHDRAW_FAIL,
      {
        method: "POST",
        body: JSON.stringify({ intent_id: intent.intent_id, reason }),
      },
    ).catch(() => undefined);

    throw new Error(uncertain ? UI.CONNECT.WITHDRAW_ERROR : reason);
  }
}

export async function earningsOverviewResponse(): Promise<Response> {
  const session = await getSession();
  if (!session?.user) {
    return Response.json({ error: UI.CONNECT.ERROR }, { status: 401 });
  }
  try {
    return Response.json(await getEarningsOverview(session.user as BillingUser));
  } catch {
    return Response.json({ error: UI.CONNECT.WITHDRAW_ERROR }, { status: 502 });
  }
}

export async function connectWithdrawResponse(request: Request): Promise<Response> {
  const session = await getSession();
  if (!session?.user) {
    return Response.json({ error: UI.CONNECT.ERROR }, { status: 401 });
  }
  try {
    const body = (await request.json()) as { amount?: string; method?: WithdrawMethod };
    if (!body.amount) {
      return Response.json({ error: "amount is required" }, { status: 400 });
    }
    const method = body.method ?? BILLING.CONNECT_WITHDRAW_BANK;
    if (
      method !== BILLING.CONNECT_WITHDRAW_BANK &&
      method !== BILLING.CONNECT_WITHDRAW_STABLECOIN
    ) {
      return Response.json({ error: "invalid method" }, { status: 400 });
    }
    const result = await requestConnectWithdraw(
      session.user as BillingUser,
      body.amount,
      method,
    );
    return Response.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : UI.CONNECT.WITHDRAW_ERROR;
    return Response.json({ error: message }, { status: 400 });
  }
}

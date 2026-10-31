import { describe, expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const withdraw = await import("../src/features/billing/lib/withdraw.ts");
const { ConnectWithdrawCard } = await import(
  "../src/features/billing/components/connect-payouts-panel.tsx"
);

describe("withdraw helpers", () => {
  test("parseWithdrawAmount accepts two-decimal USDC amounts", () => {
    expect(withdraw.parseWithdrawAmount("12.5")).toBe("12.5");
    expect(() => withdraw.parseWithdrawAmount("12.555")).toThrow();
    expect(() => withdraw.parseWithdrawAmount("0")).toThrow();
  });

  test("stablecoinWithdrawEnabled is opt-in", () => {
    expect(withdraw.stablecoinWithdrawEnabled("true")).toBe(true);
    expect(withdraw.stablecoinWithdrawEnabled("false")).toBe(false);
    expect(withdraw.stablecoinWithdrawEnabled(undefined)).toBe(false);
  });

  test("off-ramp limits call out country and entity constraints", () => {
    expect(withdraw.CONNECT_OFFRAMP_LIMITS.recipients).toMatch(/NY and HI/);
    expect(withdraw.CONNECT_OFFRAMP_LIMITS.defaultRail).toMatch(/On-chain/);
  });
});

describe("ConnectWithdrawCard", () => {
  test("ready with balance shows withdraw controls", () => {
    const html = renderToStaticMarkup(
      createElement(ConnectWithdrawCard, {
        earnings: {
          available: "20.00",
          held: "0",
          accountId: "earnings:u1",
          stablecoinEnabled: true,
          connectStatus: "ready",
          connectedAccountId: "acct_1",
        },
      }),
    );
    expect(html).toContain("Withdraw earnings");
    expect(html).toContain("20.00 USDC");
    expect(html).toContain("Bank payout");
    expect(html).toContain("NY and HI");
  });

  test("not ready hides amount entry", () => {
    const html = renderToStaticMarkup(
      createElement(ConnectWithdrawCard, {
        earnings: {
          available: "20.00",
          held: "0",
          accountId: "earnings:u1",
          stablecoinEnabled: false,
          connectStatus: "pending",
          connectedAccountId: "acct_1",
        },
      }),
    );
    expect(html).toContain("Finish Connect onboarding");
    expect(html).not.toContain('id="withdraw-amount"');
  });
});

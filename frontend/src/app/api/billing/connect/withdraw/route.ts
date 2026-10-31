import {
  connectWithdrawResponse,
  earningsOverviewResponse,
} from "@/features/billing/server/withdraw";

export async function GET() {
  return earningsOverviewResponse();
}

export async function POST(request: Request) {
  return connectWithdrawResponse(request);
}

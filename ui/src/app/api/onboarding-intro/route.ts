import { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Static onboarding intro data.
 *
 * Follows Claude Science's real chat-mode design:
 *  1. Frontend renders `greeting` immediately (no LLM call).
 *  2. Frontend submits `kickoffSystemNote` as the thread's first
 *     HumanMessage to trigger the onboarding graph.
 *  3. The LLM reads that note + the onboarding.yaml prompt and produces
 *     its own `ask_user` tool call. That interrupt is what the frontend
 *     ultimately renders as the interactive first-question card.
 *
 * The card content itself is not returned here — it comes from the LLM's
 * ask_user interrupt, keeping the prompt as the single source of truth
 * for the first question's wording.
 */
export async function GET() {
  return NextResponse.json({
    greeting:
      "你好，我是天玄·千枢科学发现平台。\n\n先花 30s 了解一下你希望我承担的工作范围，之后帮上忙也更贴合。",
    kickoffSystemNote:
      "[System] First-run onboarding started. The user has already been " +
      "greeted on screen and is already looking at your first question. " +
      "Call ask_user with your fixed first question now — output NO text, " +
      "only the tool call.",
  });
}

import type { Message } from "@langchain/langgraph-sdk";
import type { GoalState } from "@/app/types/types";
import { stripThinkTagsForDisplay } from "@/app/utils/utils";

export const THREAD_TITLE_METADATA_KEY = "internagents_title";
export const THREAD_TITLE_UPDATED_AT_METADATA_KEY =
  "internagents_title_updated_at";

export function getCustomThreadTitle(
  metadata?: Record<string, unknown> | null
): string {
  const title = metadata?.[THREAD_TITLE_METADATA_KEY];
  return typeof title === "string" ? title.trim() : "";
}

function contentToText(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    for (const block of content) {
      if (typeof block === "string") {
        return block;
      }
      if (
        block &&
        typeof block === "object" &&
        typeof (block as { text?: unknown }).text === "string"
      ) {
        return (block as { text: string }).text;
      }
    }
  }

  return "";
}

export function inferThreadTitle({
  metadata,
  goal,
  messages,
  fallback,
}: {
  metadata?: Record<string, unknown> | null;
  goal?: GoalState | null;
  messages: Array<Message | Record<string, any>>;
  fallback: string;
}): string {
  const customTitle = getCustomThreadTitle(metadata);
  if (customTitle) {
    return customTitle;
  }

  const objective = goal?.objective?.trim();
  if (objective) {
    return objective.slice(0, 50) + (objective.length > 50 ? "..." : "");
  }

  // Skip the onboarding kickoff HumanMessage — its "[System] First-run
  // onboarding started..." content is internal wiring, not a user turn.
  const firstHumanMessage = messages.find(
    (message) =>
      message?.type === "human" &&
      !contentToText(message?.content)
        .trim()
        .startsWith("[System] First-run onboarding started")
  );
  const content = contentToText(firstHumanMessage?.content).trim();
  if (content) {
    return content.slice(0, 50) + (content.length > 50 ? "..." : "");
  }

  return fallback;
}

export function inferThreadDescription(
  messages: Array<Message | Record<string, any>>
): string {
  const firstAiMessage = messages.find((message) => message?.type === "ai");
  const content = stripThinkTagsForDisplay(
    contentToText(firstAiMessage?.content)
  ).trim();
  return content ? content.slice(0, 100) : "";
}

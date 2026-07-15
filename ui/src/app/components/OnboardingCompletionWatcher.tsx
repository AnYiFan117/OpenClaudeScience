"use client";

import { useEffect, useRef } from "react";
import { useChatContext } from "@/providers/ChatProvider";

interface OnboardingCompletionWatcherProps {
  active: boolean;
  onComplete: () => void;
}

/**
 * Fires `onComplete` once when the onboarding thread comes to rest — i.e.
 * a run finished (isLoading went true→false) with at least one AI reply
 * and no pending interrupt. Mirrors CS's "user has moved past the
 * onboarding flow" fallback: even if the LLM never calls
 * `mark_workspace_onboarded`, we treat the thread reaching a natural
 * end as onboarding done.
 *
 * The watcher stays mounted for the whole onboarding session; once it
 * fires it self-disables via the `active` prop flipping (parent turns
 * off after `onComplete`).
 */
export function OnboardingCompletionWatcher({
  active,
  onComplete,
}: OnboardingCompletionWatcherProps) {
  const { isLoading, interrupt, messages } = useChatContext();
  const hadActivity = useRef(false);
  const firedRef = useRef(false);

  useEffect(() => {
    if (!active) {
      hadActivity.current = false;
      firedRef.current = false;
      return;
    }
    if (isLoading) {
      hadActivity.current = true;
      return;
    }
    if (firedRef.current) return;
    if (!hadActivity.current) return;
    if (interrupt !== undefined && interrupt !== null) return;
    const hasAiReply = (messages ?? []).some(
      (message: { type?: string }) => message?.type === "ai",
    );
    if (!hasAiReply) return;
    firedRef.current = true;
    onComplete();
  }, [active, isLoading, interrupt, messages, onComplete]);

  return null;
}

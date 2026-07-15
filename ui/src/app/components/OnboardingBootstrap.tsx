"use client";

import { useEffect, useRef } from "react";
import { useChatContext } from "@/providers/ChatProvider";

interface OnboardingBootstrapProps {
  active: boolean;
  intro: { greeting: string; kickoffSystemNote: string } | null;
}

/**
 * Fires the CS-style onboarding kickoff: submits the [System] note as the
 * thread's first HumanMessage so the ONBOARDING agent starts, reads the
 * note + its yaml prompt, and produces its own ask_user tool_call as an
 * interrupt — which the UI then renders as the real first-question card.
 *
 * Idempotent: runs at most once per mount and only when there's no active
 * thread yet and no messages have started to stream.
 */
export function OnboardingBootstrap({
  active,
  intro,
}: OnboardingBootstrapProps) {
  const { bootstrapOnboardingThread, threadId, messages, isLoading } =
    useChatContext();
  const firedRef = useRef(false);

  useEffect(() => {
    if (!active || !intro) return;
    if (firedRef.current) return;
    if (threadId) return;
    if ((messages ?? []).length > 0) return;
    if (isLoading) return;
    firedRef.current = true;
    const fired = bootstrapOnboardingThread(intro.kickoffSystemNote);
    if (!fired) {
      // guard flipped somewhere between our checks and the call — retry
      // window opens next render.
      firedRef.current = false;
    }
  }, [active, intro, threadId, messages, isLoading, bootstrapOnboardingThread]);

  return null;
}

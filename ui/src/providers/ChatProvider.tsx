"use client";

import { ReactNode, createContext, useContext } from "react";
import { Assistant } from "@langchain/langgraph-sdk";
import { type StateType, useChat } from "@/app/hooks/useChat";
import type { UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { StreamConfig } from "@/lib/config";

interface ChatProviderProps {
  children: ReactNode;
  activeAssistant: Assistant | null;
  streamConfig: StreamConfig;
  onHistoryRevalidate?: () => void;
  onGeneratedThreadId?: (threadId: string) => void;
  thread?: UseStreamThread<StateType>;
  resourceId?: string;
  resourceLabel?: string;
  workspaceId?: string;
  workspacePath?: string;
  workspaceLabel?: string;
}

export function ChatProvider({
  children,
  activeAssistant,
  streamConfig,
  onHistoryRevalidate,
  onGeneratedThreadId,
  thread,
  resourceId,
  resourceLabel,
  workspaceId,
  workspacePath,
  workspaceLabel,
}: ChatProviderProps) {
  const chat = useChat({
    activeAssistant,
    streamConfig,
    onHistoryRevalidate,
    onGeneratedThreadId,
    thread,
    resourceId,
    resourceLabel,
    workspaceId,
    workspacePath,
    workspaceLabel,
  });
  return <ChatContext.Provider value={chat}>{children}</ChatContext.Provider>;
}

export type ChatContextType = ReturnType<typeof useChat>;

export const ChatContext = createContext<ChatContextType | undefined>(
  undefined
);

export function useChatContext() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error("useChatContext must be used within a ChatProvider");
  }
  return context;
}

"use client";

import React, { useMemo, useState, useCallback } from "react";
import { SubAgentIndicator } from "@/app/components/SubAgentIndicator";
import { ToolCallBox } from "@/app/components/ToolCallBox";
import { MarkdownContent } from "@/app/components/MarkdownContent";
import { FileIcon } from "lucide-react";
import type {
  ChatAttachment,
  SubAgent,
  ToolCall,
  ActionRequest,
  ReviewConfig,
} from "@/app/types/types";
import { Message } from "@langchain/langgraph-sdk";
import {
  extractImageUrlsFromMessageContent,
  extractSubAgentContent,
  extractVisibleStringFromMessageContent,
} from "@/app/utils/utils";
import { useLanguage } from "@/app/hooks/useLanguage";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: Message;
  toolCalls: ToolCall[];
  showAvatar?: boolean;
  isLoading?: boolean;
  actionRequestsMap?: Map<string, ActionRequest>;
  reviewConfigsMap?: Map<string, ReviewConfig>;
  ui?: any[];
  stream?: any;
  onResumeInterrupt?: (value: any) => void;
  graphId?: string;
  runtimeMuted?: boolean;
  showTerminalToolIssueNotice?: boolean;
  onOpenAttachment?: (path: string) => void;
  workspaceRoot?: string;
  // Frame results keyed by frame_id, populated by ChatInterface by scanning
  // all thread messages for delegate/collect/wait_for_notification tool
  // results. Used to fill in the output side of cards created from
  // delegate_subframes tool calls.
  frameOutputMap?: Map<
    string,
    {
      status: string;
      output?: Record<string, unknown>;
      name?: string;
      agent_name?: string;
    }
  >;
}

export const ChatMessage = React.memo<ChatMessageProps>(
  ({
    message,
    toolCalls,
    showAvatar,
    isLoading,
    actionRequestsMap,
    reviewConfigsMap,
    ui,
    stream,
    onResumeInterrupt,
    graphId,
    runtimeMuted,
    showTerminalToolIssueNotice,
    onOpenAttachment,
    workspaceRoot,
    frameOutputMap,
  }) => {
    const { t } = useLanguage();
    const isUser = message.type === "human";
    const messageContent = extractVisibleStringFromMessageContent(message);
    const imageUrls = useMemo(
      () => (isUser ? extractImageUrlsFromMessageContent(message) : []),
      [isUser, message]
    );
    const attachments = useMemo(() => {
      const rawAttachments = message.additional_kwargs?.attachments;
      return Array.isArray(rawAttachments)
        ? (rawAttachments as ChatAttachment[])
        : [];
    }, [message.additional_kwargs]);
    const hasContent = messageContent && messageContent.trim() !== "";
    const showMutedRuntime = Boolean(isLoading || runtimeMuted);
    const visibleFileAttachments = attachments.filter(
      (attachment) => attachment.kind !== "image"
    );
    const hasUserAttachments =
      isUser && (imageUrls.length > 0 || visibleFileAttachments.length > 0);
    const hasToolCalls = toolCalls.length > 0;
    const hasTerminalToolIssue = toolCalls.some(
      (toolCall) =>
        Boolean(toolCall.result) &&
        (toolCall.status === "error" || toolCall.status === "interrupted")
    );
    const subAgents = useMemo(() => {
      const cards: SubAgent[] = [];

      // Legacy deepagents `task` tool subagents (kept for compatibility if
      // some skills still declare `subagents`).
      for (const toolCall of toolCalls) {
        if (
          toolCall.name === "task" &&
          toolCall.args["subagent_type"] &&
          toolCall.args["subagent_type"] !== "" &&
          toolCall.args["subagent_type"] !== null
        ) {
          const subagentType = (toolCall.args as Record<string, unknown>)[
            "subagent_type"
          ] as string;
          cards.push({
            id: toolCall.id,
            name: toolCall.name,
            subAgentName: subagentType,
            input: toolCall.args,
            output: toolCall.result ? { result: toolCall.result } : undefined,
            status: toolCall.status,
          } as SubAgent);
        }
      }

      // CS-style async `delegate_subframes(requests=[...])` — one card per
      // request in the batch. Frame IDs come from the tool's initial result
      // (running descriptors); status/output later filled in by frameOutputMap
      // once collect_subframes / wait_for_notification lands.
      for (const toolCall of toolCalls) {
        if (toolCall.name !== "delegate_subframes") continue;
        const args = toolCall.args as Record<string, unknown> | undefined;
        const requests = Array.isArray(args?.requests)
          ? (args!.requests as Array<Record<string, unknown>>)
          : [];
        // Parse the tool's own result to align request[i] with frame_id[i].
        let framesFromResult: Array<Record<string, unknown>> = [];
        try {
          const parsedResult =
            typeof toolCall.result === "string"
              ? JSON.parse(toolCall.result)
              : (toolCall.result as unknown);
          if (
            parsedResult &&
            typeof parsedResult === "object" &&
            Array.isArray((parsedResult as Record<string, unknown>).frames)
          ) {
            framesFromResult = (parsedResult as {
              frames: Array<Record<string, unknown>>;
            }).frames;
          }
        } catch {
          // If we can't parse yet (still streaming) we still emit cards
          // keyed by index without frame_id — output stays undefined.
        }
        const pairCount = Math.max(requests.length, framesFromResult.length);
        for (let i = 0; i < pairCount; i++) {
          const req = requests[i] || {};
          const frame = framesFromResult[i] || {};
          const frameId =
            typeof frame.frame_id === "string" ? frame.frame_id : "";
          const cardName =
            (typeof req.name === "string" && req.name) ||
            (typeof frame.name === "string" && frame.name) ||
            (typeof req.agent_name === "string" && req.agent_name) ||
            "subagent";
          const objective =
            (typeof req.objective === "string" && req.objective) ||
            (typeof req.task === "string" && req.task) ||
            "";

          // Look up terminal state from frameOutputMap (populated by
          // collect_subframes / wait_for_notification completions).
          const observed = frameId
            ? frameOutputMap?.get(frameId)
            : undefined;
          const derivedStatus: SubAgent["status"] = (() => {
            const s = observed?.status ?? (frame.status as string | undefined);
            if (s === "completed") return "completed";
            if (s === "failed" || s === "error") return "error";
            if (s === "running") return "active";
            if (!s) return "pending";
            return "active";
          })();
          const output = observed?.output;

          cards.push({
            id: frameId || `${toolCall.id}:${i}`,
            name: "delegate_subframes",
            subAgentName: String(cardName),
            input: { objective, ...req },
            output: output ? (output as Record<string, unknown>) : undefined,
            status: derivedStatus,
          });
        }
      }

      return cards;
    }, [toolCalls, frameOutputMap]);

    const [expandedSubAgents, setExpandedSubAgents] = useState<
      Record<string, boolean>
    >({});
    const isSubAgentExpanded = useCallback(
      (id: string) => expandedSubAgents[id] ?? false,
      [expandedSubAgents]
    );
    const toggleSubAgent = useCallback((id: string) => {
      setExpandedSubAgents((prev) => ({
        ...prev,
        [id]: !(prev[id] ?? false),
      }));
    }, []);

    return (
      <div
        className={cn(
          "flex w-full max-w-full overflow-x-hidden",
          isUser ? "flex-row-reverse" : "gap-3"
        )}
      >
        {!isUser && (
          <div className="mt-4 flex h-7 w-7 shrink-0 items-start justify-center">
            {showAvatar && (
              <div
                className="border-primary/20 text-primary-foreground flex h-7 w-7 items-center justify-center rounded-md border bg-primary text-xs font-semibold tracking-wide shadow-sm shadow-black/[0.035]"
                title="天玄·千枢科学发现平台"
                aria-label="天玄·千枢科学发现平台"
              >
                IA
              </div>
            )}
          </div>
        )}
        <div
          className={cn(
            "min-w-0 max-w-full",
            isUser ? "max-w-[70%]" : "w-full"
          )}
        >
          {hasContent && (
            <div className={cn("relative flex items-end gap-0")}>
              <div
                className={cn(
                  "mt-4 overflow-hidden break-words text-sm font-normal leading-[150%]",
                  isUser
                    ? "border-primary/15 rounded-lg rounded-br-sm border px-3 py-2 text-foreground shadow-sm shadow-black/[0.025]"
                    : "text-foreground"
                )}
                style={
                  isUser
                    ? { backgroundColor: "var(--color-user-message-bg)" }
                    : undefined
                }
              >
                {isUser ? (
                  <p className="m-0 whitespace-pre-wrap break-words text-sm leading-relaxed">
                    {messageContent}
                  </p>
                ) : hasContent ? (
                  <MarkdownContent
                    content={messageContent}
                    onOpenWorkspacePath={onOpenAttachment}
                    workspaceRoot={workspaceRoot}
                  />
                ) : null}
              </div>
            </div>
          )}
          {hasUserAttachments && (
            <div className="mt-2 flex flex-wrap justify-end gap-2">
              {imageUrls.map((imageUrl, index) => (
                <img
                  key={`${message.id}-image-${index}`}
                  src={imageUrl}
                  alt=""
                  className="max-h-44 max-w-56 rounded-md border border-border bg-card object-contain shadow-sm shadow-black/[0.025]"
                />
              ))}
              {visibleFileAttachments.map((attachment) => (
                <button
                  key={attachment.id}
                  type="button"
                  className={cn(
                    "flex max-w-56 items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 text-left text-xs text-muted-foreground shadow-sm shadow-black/[0.025]",
                    attachment.workspacePath &&
                      "hover:border-primary/30 transition-colors hover:text-foreground"
                  )}
                  onClick={() => {
                    if (attachment.workspacePath) {
                      onOpenAttachment?.(attachment.workspacePath);
                    }
                  }}
                  disabled={!attachment.workspacePath}
                  title={
                    attachment.workspacePath
                      ? t("openWorkspacePath", {
                          path: attachment.workspacePath,
                        })
                      : attachment.name
                  }
                >
                  <FileIcon className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 truncate">{attachment.name}</span>
                </button>
              ))}
            </div>
          )}
          {!isUser &&
            !hasContent &&
            hasTerminalToolIssue &&
            showTerminalToolIssueNotice && (
              <div className="mt-4 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm leading-6 text-foreground">
                {t("terminalToolIssueNotice")}
              </div>
            )}
          {hasToolCalls && (
            <div className="mt-4 flex w-full flex-col">
              {toolCalls.map((toolCall: ToolCall) => {
                if (toolCall.name === "task") return null;
                if (toolCall.name === "delegate_subframes") return null;
                const toolCallGenUiComponent = ui?.find(
                  (u) => u.metadata?.tool_call_id === toolCall.id
                );
                const actionRequest = actionRequestsMap?.get(toolCall.name);
                const reviewConfig = reviewConfigsMap?.get(toolCall.name);
                return (
                  <ToolCallBox
                    key={toolCall.id}
                    toolCall={toolCall}
                    uiComponent={toolCallGenUiComponent}
                    stream={stream}
                    graphId={graphId}
                    actionRequest={actionRequest}
                    reviewConfig={reviewConfig}
                    onResume={onResumeInterrupt}
                    isLoading={isLoading}
                    muted={showMutedRuntime}
                  />
                );
              })}
            </div>
          )}
          {!isUser && subAgents.length > 0 && (
            <div className="flex w-fit max-w-full flex-col gap-4">
              {subAgents.map((subAgent) => (
                <div
                  key={subAgent.id}
                  className="flex w-full flex-col gap-2"
                >
                  <div className="flex items-end gap-2">
                    <div className="w-[calc(100%-100px)]">
                      <SubAgentIndicator
                        subAgent={subAgent}
                        onClick={() => toggleSubAgent(subAgent.id)}
                        isExpanded={isSubAgentExpanded(subAgent.id)}
                        muted={showMutedRuntime}
                      />
                    </div>
                  </div>
                  {isSubAgentExpanded(subAgent.id) && (
                    <div className="w-full max-w-full">
                      <div
                        className={cn(
                          "rounded-md border border-border bg-card p-4 shadow-sm shadow-black/[0.025]",
                          showMutedRuntime &&
                            "border-border/30 bg-muted/5 text-muted-foreground/70 shadow-none"
                        )}
                      >
                        <h4
                          className={cn(
                            "text-primary/70 mb-2 text-xs font-semibold uppercase tracking-wider",
                            showMutedRuntime && "text-muted-foreground/70"
                          )}
                        >
                          Input
                        </h4>
                        <div className="mb-4">
                          <MarkdownContent
                            content={extractSubAgentContent(subAgent.input)}
                            onOpenWorkspacePath={onOpenAttachment}
                            workspaceRoot={workspaceRoot}
                            className={cn(
                              showMutedRuntime &&
                                "text-muted-foreground/70 [&_a]:!text-muted-foreground/70 [&_code]:!text-muted-foreground/70"
                            )}
                          />
                        </div>
                        {subAgent.output && (
                          <>
                            <h4
                              className={cn(
                                "text-primary/70 mb-2 text-xs font-semibold uppercase tracking-wider",
                                showMutedRuntime && "text-muted-foreground/70"
                              )}
                            >
                              Output
                            </h4>
                            <MarkdownContent
                              content={extractSubAgentContent(subAgent.output)}
                              onOpenWorkspacePath={onOpenAttachment}
                              workspaceRoot={workspaceRoot}
                              className={cn(
                                showMutedRuntime &&
                                  "text-muted-foreground/70 [&_a]:!text-muted-foreground/70 [&_code]:!text-muted-foreground/70"
                              )}
                            />
                          </>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }
);

ChatMessage.displayName = "ChatMessage";

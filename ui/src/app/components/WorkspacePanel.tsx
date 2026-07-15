"use client";

import { useEffect, useState, type ReactNode } from "react";
import { FolderOpen, MessageSquare } from "lucide-react";
import { ThreadList } from "@/app/components/ThreadList";
import { WorkspaceExplorer } from "@/app/components/WorkspaceExplorer";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { LocalWorkspace, WorkspaceEntry } from "@/app/types/workspace";
import { useLanguage } from "@/app/hooks/useLanguage";

interface WorkspacePanelProps {
  selectedFilePath?: string | null;
  onFileSelect: (entry: WorkspaceEntry) => void;
  onThreadSelect: (id: string) => void;
  onNewThread?: () => void;
  onMutateReady?: (mutate: () => void) => void;
  resourceId?: string;
  assistantId?: string;
  workspaceId?: string;
  workspaceRefreshKey?: number;
  activeWorkspace?: LocalWorkspace | null;
  onCompactChange?: (compact: boolean) => void;
}

export function WorkspacePanel({
  selectedFilePath,
  onFileSelect,
  onThreadSelect,
  onNewThread,
  onMutateReady,
  resourceId,
  assistantId,
  workspaceId,
  workspaceRefreshKey,
  activeWorkspace,
  onCompactChange,
}: WorkspacePanelProps) {
  const { t } = useLanguage();
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [threadsCollapsed, setThreadsCollapsed] = useState(false);
  const showWorkspace = !workspaceCollapsed;
  const showThreads = !threadsCollapsed;
  const hasCollapsedRail = workspaceCollapsed || threadsCollapsed;
  const compact = !showWorkspace && !showThreads;

  useEffect(() => {
    onCompactChange?.(compact);
  }, [compact, onCompactChange]);

  const workspaceExplorer = (
    <WorkspaceExplorer
      selectedPath={selectedFilePath}
      resourceId={resourceId}
      workspaceId={workspaceId}
      refreshKey={workspaceRefreshKey}
      activeWorkspace={activeWorkspace}
      onFileSelect={onFileSelect}
      onCollapse={() => setWorkspaceCollapsed(true)}
    />
  );

  const threadList = (
    <ThreadList
      onThreadSelect={onThreadSelect}
      onNewThread={onNewThread}
      onMutateReady={onMutateReady}
      onCollapse={() => setThreadsCollapsed(true)}
      resourceId={resourceId}
      assistantId={assistantId}
      workspaceId={workspaceId}
    />
  );

  return (
    <div className="flex h-full bg-sidebar">
      {hasCollapsedRail && (
        <div className="flex w-11 shrink-0 flex-col items-center gap-1 border-r border-border bg-card/70 py-2">
          {workspaceCollapsed && (
            <CollapsedPanelButton
              label={t("expandProjectFiles")}
              onClick={() => setWorkspaceCollapsed(false)}
            >
              <FolderOpen className="h-4 w-4" />
            </CollapsedPanelButton>
          )}
          {threadsCollapsed && (
            <CollapsedPanelButton
              label={t("expandSessions")}
              onClick={() => setThreadsCollapsed(false)}
            >
              <MessageSquare className="h-4 w-4" />
            </CollapsedPanelButton>
          )}
        </div>
      )}

      <div className={cn("relative min-w-0 flex-1", compact && "hidden")}>
        {showWorkspace && showThreads ? (
          <ResizablePanelGroup
            direction="vertical"
            autoSaveId="internagents-left-panel"
          >
            <ResizablePanel
              id="workspace-files"
              order={1}
              defaultSize={54}
              minSize={28}
              className="relative min-h-[220px]"
            >
              {workspaceExplorer}
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel
              id="workspace-threads"
              order={2}
              defaultSize={46}
              minSize={26}
              className="relative min-h-[220px]"
            >
              {threadList}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : null}

        {showWorkspace && !showThreads ? (
          <div className="relative h-full">{workspaceExplorer}</div>
        ) : null}

        {!showWorkspace && showThreads ? (
          <div className="relative h-full">{threadList}</div>
        ) : null}
      </div>
    </div>
  );
}

function CollapsedPanelButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-primary"
          aria-label={label}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent
        side="right"
        align="center"
        sideOffset={8}
        className="whitespace-nowrap"
      >
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

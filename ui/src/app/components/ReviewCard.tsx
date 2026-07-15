"use client";

import React, { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Search,
  Loader2,
  CircleCheckBigIcon,
  AlertTriangle,
  AlertOctagon,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ReviewEntry } from "@/app/types/types";
import { cn } from "@/lib/utils";

interface ReviewCardProps {
  /** null → loading placeholder (reviewer is running) */
  entry: ReviewEntry | null;
}

const VERDICT_META: Record<
  "pass" | "warn" | "fail" | "unknown" | "loading" | "failed",
  { label: string; className: string; Icon: React.ComponentType<{ size?: number; className?: string }> }
> = {
  pass: {
    label: "pass",
    className: "text-emerald-600 dark:text-emerald-400",
    Icon: CircleCheckBigIcon,
  },
  warn: {
    label: "warn",
    className: "text-amber-600 dark:text-amber-400",
    Icon: AlertTriangle,
  },
  fail: {
    label: "fail",
    className: "text-destructive",
    Icon: AlertOctagon,
  },
  unknown: {
    label: "unknown",
    className: "text-muted-foreground",
    Icon: Search,
  },
  loading: {
    label: "reviewing…",
    className: "text-muted-foreground",
    Icon: Loader2,
  },
  failed: {
    label: "review failed",
    className: "text-destructive",
    Icon: XCircle,
  },
};

export const ReviewCard = React.memo<ReviewCardProps>(({ entry }) => {
  const isLoading = entry === null;
  const isFailed = entry?.status === "failed";

  // Verdict key resolution: loading → 'loading'; failed status → 'failed';
  // done → use entry.verdict (default 'unknown').
  const verdictKey: keyof typeof VERDICT_META = isLoading
    ? "loading"
    : isFailed
    ? "failed"
    : entry?.verdict ?? "unknown";

  const meta = VERDICT_META[verdictKey];

  // Default expanded: warn / fail / failed. Collapsed: pass / loading / unknown.
  const defaultExpanded = useMemo(
    () => verdictKey === "warn" || verdictKey === "fail" || verdictKey === "failed",
    [verdictKey]
  );
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  const issues = entry?.issues ?? [];
  const suggestions = entry?.suggestions ?? [];
  const hasBody =
    !isLoading && (issues.length > 0 || suggestions.length > 0 || Boolean(entry?.error));

  const useMutedStyle = isLoading || verdictKey === "pass";

  return (
    <div
      className={cn(
        "w-full overflow-hidden rounded-md border border-transparent outline-none transition-[background-color,border-color] duration-200 hover:border-border hover:bg-accent/60",
        isExpanded && hasBody && "border-border bg-accent/60",
        useMutedStyle &&
          "border-border/30 bg-muted/5 text-muted-foreground/70 shadow-none hover:border-border/40 hover:bg-muted/10",
        useMutedStyle && isExpanded && hasBody && "bg-muted/10"
      )}
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-full items-center justify-between gap-2 border-none px-2 py-2 text-left shadow-none outline-none focus-visible:ring-0 focus-visible:ring-offset-0 disabled:cursor-default"
        disabled={!hasBody}
      >
        <div className="flex w-full items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <meta.Icon
              size={14}
              className={cn(
                meta.className,
                isLoading && "animate-spin"
              )}
            />
            <span
              className={cn(
                "text-sm font-medium text-foreground",
                useMutedStyle && "text-muted-foreground/70"
              )}
            >
              review
            </span>
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[11px] font-medium leading-none",
                meta.className,
                "border-current/30"
              )}
            >
              {meta.label}
            </span>
            {!isLoading && entry && entry.bounce_count > 0 && (
              <span
                className="rounded-full border border-border px-1.5 py-0.5 text-[11px] font-medium leading-none text-muted-foreground"
                title="Consecutive veto bounces this arc"
              >
                bounce {entry.bounce_count}
              </span>
            )}
          </div>
          {hasBody &&
            (isExpanded ? (
              <ChevronUp size={14} className="shrink-0 text-muted-foreground" />
            ) : (
              <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
            ))}
        </div>
      </Button>

      {isExpanded && hasBody && (
        <div className="space-y-3 px-4 pb-4 pt-1">
          {entry?.error && (
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-destructive">
                Error
              </h4>
              <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-destructive/30 bg-muted/40 p-2 font-mono text-xs leading-6 text-foreground">
                {entry.error}
              </pre>
            </div>
          )}
          {issues.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Issues
              </h4>
              <ul className="space-y-1 text-sm leading-6 text-foreground">
                {issues.map((issue, i) => (
                  <li key={i} className="list-inside list-disc">
                    {issue}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {suggestions.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Suggestions
              </h4>
              <ul className="space-y-1 text-sm leading-6 text-foreground">
                {suggestions.map((s, i) => (
                  <li key={i} className="list-inside list-disc">
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

ReviewCard.displayName = "ReviewCard";

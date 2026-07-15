"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { HelpCircle, MessageCircle, Send } from "lucide-react";
import { cn } from "@/lib/utils";

export interface AskUserOption {
  label: string;
  value: string;
  description?: string;
}

export interface AskUserPrompt {
  type: "ask_user";
  question: string;
  description?: string | null;
  options?: AskUserOption[];
  allow_other?: boolean;
}

interface AskUserInterruptProps {
  prompt: AskUserPrompt;
  onResume: (value: string) => void;
  isLoading?: boolean;
}

export function AskUserInterrupt({
  prompt,
  onResume,
  isLoading,
}: AskUserInterruptProps) {
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherText, setOtherText] = useState("");
  const options = Array.isArray(prompt.options) ? prompt.options : [];
  const allowOther = prompt.allow_other !== false;
  const description = prompt.description ?? null;

  const handleOption = (value: string) => {
    if (isLoading) return;
    onResume(value);
  };

  const handleSubmitOther = () => {
    const text = otherText.trim();
    if (!text || isLoading) return;
    onResume(text);
  };

  return (
    <div
      className={cn(
        "rounded-lg border border-primary/30 bg-primary/5 p-4",
        "flex flex-col gap-3",
      )}
      role="dialog"
      aria-labelledby="ask-user-question"
    >
      <div className="flex items-start gap-2">
        <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary/15">
          <HelpCircle className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <p
            id="ask-user-question"
            className="text-sm font-medium text-foreground"
          >
            {prompt.question}
          </p>
          {description ? (
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>

      {options.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {options.map((option, idx) => (
            <Button
              key={`${option.value}-${idx}`}
              variant="outline"
              size="sm"
              disabled={isLoading}
              onClick={() => handleOption(option.value)}
              className="justify-start text-left h-auto py-2 px-3"
            >
              <div className="flex flex-col items-start gap-0.5 w-full">
                <span className="text-sm">{option.label}</span>
                {option.description ? (
                  <span className="text-xs text-muted-foreground font-normal">
                    {option.description}
                  </span>
                ) : null}
              </div>
            </Button>
          ))}
        </div>
      ) : null}

      {allowOther ? (
        <div className="flex flex-col gap-2">
          {!otherOpen ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={isLoading}
              onClick={() => setOtherOpen(true)}
              className="justify-start text-left"
            >
              <MessageCircle className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
              其他…
            </Button>
          ) : (
            <div className="flex flex-col gap-2 rounded-md border border-border/60 bg-background/40 p-2">
              <Textarea
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                placeholder="用自己的话回答…"
                rows={2}
                disabled={isLoading}
                className="resize-none text-sm"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmitOther();
                  }
                }}
              />
              <div className="flex gap-2 justify-end">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={isLoading}
                  onClick={() => {
                    setOtherOpen(false);
                    setOtherText("");
                  }}
                >
                  取消
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  disabled={isLoading || !otherText.trim()}
                  onClick={handleSubmitOther}
                >
                  <Send className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
                  提交
                </Button>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

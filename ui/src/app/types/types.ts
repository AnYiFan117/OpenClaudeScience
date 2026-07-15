export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: "pending" | "completed" | "error" | "interrupted";
}

export interface SubAgent {
  id: string;
  name: string;
  subAgentName: string;
  input: Record<string, unknown>;
  output?: Record<string, unknown>;
  status: "pending" | "active" | "completed" | "error";
}

export interface FileItem {
  path: string;
  content: string;
}

export interface TodoItem {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed";
  updatedAt?: Date;
}

export interface GoalState {
  id: string;
  threadId?: string | null;
  objective: string;
  status: "active" | "blocked" | "complete";
  tokenBudget?: number | null;
  tokensUsed: number;
  timeUsedSeconds: number;
  createdAt: number;
  updatedAt: number;
}

export interface ThreadSkillItem {
  key: string;
  name: string;
  description: string;
  relativePath: string;
  folderName: string;
  addedAt: number;
}

export interface ThreadSkillsState {
  revision: number;
  active: ThreadSkillItem[];
}

export interface ChatAttachment {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  kind: "image" | "text" | "pdf" | "file";
  dataUrl?: string;
  text?: string;
  workspacePath?: string;
  extractedWorkspacePath?: string;
  extractedTextSize?: number;
  pageCount?: number;
  extractedPageCount?: number;
  truncated?: boolean;
  extractionError?: string;
  error?: string;
}

export interface Thread {
  id: string;
  title: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface InterruptData {
  value: any;
  ns?: string[];
  scope?: string;
}

export interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

export interface ReviewConfig {
  actionName?: string;
  action_name?: string;
  allowedDecisions?: string[];
  allowed_decisions?: string[];
}

export interface ToolApprovalInterruptData {
  action_requests: ActionRequest[];
  review_configs?: ReviewConfig[];
}

// Reviewer subgraph output — kept in a separate `state.reviews` channel
// from `state.messages`, so review content never enters the LLM's input.
// Backend writer: internagents/verifier_dispatch_middleware.py:_build_review_entry
export interface ReviewEntry {
  id: string;
  at_message_index: number;
  status: "done" | "failed";
  bounce_count: number;
  timestamp: number;
  verdict?: "pass" | "warn" | "fail" | "unknown";
  issues?: string[];
  suggestions?: string[];
  error?: string;
}

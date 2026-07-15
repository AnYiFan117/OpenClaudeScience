import useSWRInfinite from "swr/infinite";
import { useMemo } from "react";
import { Client, type Thread } from "@langchain/langgraph-sdk";
import { useRemoteAgent } from "@/providers/ClientProvider";
import {
  inferThreadDescription,
  inferThreadTitle,
} from "@/app/utils/threadTitle";
import {
  loadPendingRunInputPreview,
  pendingRunValues,
} from "@/lib/pending-run-input";
import {
  messagesFromValues,
  resolveThreadListValues,
} from "@/lib/thread-state";

// Runtime (port 22024) is architecturally unable to answer for coordinator
// thread_ids in the current deployment: threads live on the coordinator
// (port 2024) and the runtime does not index by that id, so every call
// 404s. Once we see the first 404, flip this flag process-wide so the
// SWR interval doesn't keep re-firing three requests per listed thread.
let RUNTIME_THREAD_INDEX_UNAVAILABLE = false;

function isNotFoundError(error: unknown): boolean {
  if (!error) return false;
  const status = (error as { status?: unknown }).status;
  if (status === 404) return true;
  const msg = String((error as { message?: unknown }).message ?? error);
  return msg.includes("HTTP 404") || msg.includes("Not Found");
}

async function callRuntime<T>(fn: () => Promise<T>): Promise<T | undefined> {
  if (RUNTIME_THREAD_INDEX_UNAVAILABLE) return undefined;
  try {
    return await fn();
  } catch (error) {
    if (isNotFoundError(error)) {
      if (!RUNTIME_THREAD_INDEX_UNAVAILABLE) {
        RUNTIME_THREAD_INDEX_UNAVAILABLE = true;
        console.info(
          "[useThreads] Runtime does not index coordinator thread_ids " +
            "(first 404 observed). Skipping runtime fallbacks for the " +
            "remainder of this session. Coordinator state drives the list."
        );
      }
      return undefined;
    }
    throw error;
  }
}

export interface ThreadItem {
  id: string;
  updatedAt: Date;
  status: Thread["status"];
  title: string;
  description: string;
  assistantId?: string;
  metadata: Record<string, unknown>;
  archived: boolean;
}

const DEFAULT_PAGE_SIZE = 20;

async function resolveThreadValues(
  thread: Thread,
  client: Client,
  runtimeClient: Client | null
): Promise<unknown> {
  let pendingRunStatus: string | undefined;

  return resolveThreadListValues({
    threadValues: thread.values,
    loadMainStateValues: async () =>
      (await client.threads.getState(thread.thread_id)).values,
    loadPendingValues: async () => {
      const pendingRunPreview = await loadPendingRunInputPreview(
        client,
        thread.thread_id
      );
      pendingRunStatus = pendingRunPreview?.status;
      if (!pendingRunPreview) return undefined;
      return {
        ...(thread.values && typeof thread.values === "object"
          ? thread.values
          : {}),
        ...pendingRunValues(pendingRunPreview),
      };
    },
    preferRuntimeValuesBeforePending: () =>
      pendingRunStatus === "pending" || pendingRunStatus === "running",
    loadRuntimeStateValues: runtimeClient
      ? async () =>
          callRuntime(async () =>
            (await runtimeClient.threads.getState(thread.thread_id)).values
          )
      : undefined,
    loadRuntimeThreadValues: runtimeClient
      ? async () =>
          callRuntime(async () =>
            (await runtimeClient.threads.get(thread.thread_id)).values
          )
      : undefined,
    loadRuntimeHistoryValues: runtimeClient
      ? async () =>
          (await callRuntime(async () => {
            const history = await runtimeClient.threads.getHistory(
              thread.thread_id,
              {
                limit: 80,
              }
            );
            return history.map((state) => state.values);
          })) ?? []
      : undefined,
  });
}

export function useThreads(props: {
  status?: Thread["status"];
  limit?: number;
  resourceId?: string;
  runtimeUrl?: string;
  assistantId?: string;
  workspaceId?: string;
  archived?: boolean;
}) {
  const remoteAgent = useRemoteAgent();
  const runtimeClient = useMemo(
    () =>
      props.runtimeUrl
        ? new Client({
            apiUrl: props.runtimeUrl,
            defaultHeaders: { "Content-Type": "application/json" },
          })
        : null,
    [props.runtimeUrl]
  );
  const pageSize = props.limit || DEFAULT_PAGE_SIZE;
  const archived = props.archived ?? false;

  return useSWRInfinite(
    (pageIndex: number, previousPageData: ThreadItem[] | null) => {
      if (previousPageData && previousPageData.length === 0) {
        return null;
      }

      return {
        kind: "threads" as const,
        pageIndex,
        pageSize,
        deploymentUrl: remoteAgent.url,
        assistantId: props.assistantId || remoteAgent.graphName,
        status: props?.status,
        resourceId: props.resourceId,
        runtimeUrl: props.runtimeUrl,
        workspaceId: props.workspaceId,
        archived,
      };
    },
    async ({
      assistantId,
      status,
      resourceId,
      workspaceId,
      pageIndex,
      pageSize,
      archived,
    }: {
      kind: "threads";
      pageIndex: number;
      pageSize: number;
      deploymentUrl: string;
      assistantId: string;
      status?: Thread["status"];
      resourceId?: string;
      runtimeUrl?: string;
      workspaceId?: string;
      archived: boolean;
    }) => {
      const threads = await remoteAgent.searchThreads({
        limit: pageSize,
        offset: pageIndex * pageSize,
        status,
        metadata: {
          ...(resourceId ? { resource_id: resourceId } : {}),
          ...(workspaceId ? { internagents_workspace_id: workspaceId } : {}),
          ...(archived ? { internagents_archived: true } : {}),
        },
      });

      const resolvedThreads = await Promise.all(
        threads.map(async (thread) => ({
          thread,
          values: await resolveThreadValues(
            thread,
            remoteAgent.client,
            runtimeClient
          ),
        }))
      );

      return resolvedThreads
        .map(({ thread, values }): ThreadItem => {
          let title = "Untitled Thread";
          let description = "";
          const metadata =
            thread.metadata && typeof thread.metadata === "object"
              ? (thread.metadata as Record<string, unknown>)
              : {};

          try {
            const valuesRecord =
              values && typeof values === "object" ? (values as any) : null;
            const goal = valuesRecord?.goal;
            const messages = messagesFromValues(values);
            title = inferThreadTitle({
              metadata,
              goal,
              messages,
              fallback: title,
            });
            if (goal?.objective) {
              description = `Goal ${goal.status || "active"}`;
            } else {
              description = inferThreadDescription(messages);
            }
          } catch {
            title = `会话 ${thread.thread_id.slice(0, 8)}`;
          }

          return {
            id: thread.thread_id,
            updatedAt: new Date(thread.updated_at),
            status: thread.status,
            title,
            description,
            assistantId,
            metadata,
            archived: metadata.internagents_archived === true,
          };
        })
        .filter((thread) => (archived ? thread.archived : !thread.archived));
    },
    {
      revalidateFirstPage: true,
      revalidateOnFocus: true,
    }
  );
}

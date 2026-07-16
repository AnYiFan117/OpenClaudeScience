import useSWRInfinite from "swr/infinite";
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
  client: Client
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
  });
}

export function useThreads(props: {
  status?: Thread["status"];
  limit?: number;
  resourceId?: string;
  assistantId?: string;
  workspaceId?: string;
  archived?: boolean;
}) {
  const remoteAgent = useRemoteAgent();
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
          values: await resolveThreadValues(thread, remoteAgent.client),
        }))
      );

      return resolvedThreads
        .map(({ thread, values }): ThreadItem => {
          const metadata =
            thread.metadata && typeof thread.metadata === "object"
              ? (thread.metadata as Record<string, unknown>)
              : {};
          // Threads bootstrapped by OnboardingBootstrap contain only a
          // '[System] First-run onboarding started…' kickoff — inferThreadTitle
          // strips that and, absent any other candidate, would fall through
          // to a generic label. Give onboarding threads a purpose-matching
          // fallback so the sidebar reads '开始使用' instead of
          // 'Untitled Thread' whenever the user hasn't yet answered the
          // first ask_user (or bailed out before any real message).
          const isOnboardingGraph =
            metadata?.graph_id === "agent_onboarding_local";
          let title = isOnboardingGraph ? "开始使用" : "Untitled Thread";
          let description = "";

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

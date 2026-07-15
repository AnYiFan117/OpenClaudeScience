import { promises as fs } from "fs";
import { realpathSync, existsSync } from "fs";
import os from "os";
import path from "path";
import crypto from "crypto";
import { NextRequest, NextResponse } from "next/server";
import {
  getWorkspaceResource,
  getWorkspaceRoot,
} from "@/app/api/workspace/_lib/workspace";

export const runtime = "nodejs";

function deepagentConfigPath(): string {
  return path.join(getWorkspaceRoot(), "deepagent.config.json");
}

/**
 * Compute the workspace id for a resource, matching Python's
 * `internagents.frame_service._workspace_id_from_resource` exactly:
 *   SHA256(Path(workspace).expanduser().resolve())[:16]
 * so config entries written by `mark_workspace_onboarded` line up.
 */
function computeWorkspaceId(resourceId: string): string {
  try {
    const resource = getWorkspaceResource(resourceId);
    const raw = resource.workspace;
    if (!raw) {
      return `resource:${resourceId}`;
    }
    let expanded = raw;
    if (expanded.startsWith("~")) {
      expanded = path.join(os.homedir(), expanded.slice(1));
    }
    let absolute = path.resolve(expanded);
    if (existsSync(absolute)) {
      try {
        absolute = realpathSync(absolute);
      } catch {
        // fall back to path.resolve output
      }
    }
    return crypto.createHash("sha256").update(absolute).digest("hex").slice(0, 16);
  } catch {
    return `resource:${resourceId}`;
  }
}

async function readConfig(): Promise<Record<string, unknown>> {
  try {
    const content = await fs.readFile(deepagentConfigPath(), "utf8");
    return JSON.parse(content) as Record<string, unknown>;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return {};
    }
    throw error;
  }
}

async function writeConfig(config: Record<string, unknown>): Promise<void> {
  const target = deepagentConfigPath();
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, JSON.stringify(config, null, 2) + "\n", "utf8");
}

function isMarkedComplete(
  config: Record<string, unknown>,
  workspaceId: string,
): boolean {
  const table = config.onboarding_completed_workspaces;
  if (!table || typeof table !== "object") return false;
  const entry = (table as Record<string, unknown>)[workspaceId];
  return entry === true;
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const resourceId = url.searchParams.get("resource") || "local";
  try {
    const workspaceId = computeWorkspaceId(resourceId);
    const config = await readConfig();
    return NextResponse.json({
      complete: isMarkedComplete(config, workspaceId),
      workspaceId,
      resourceId,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: (error as Error).message || "Failed to read onboarding status",
        complete: false,
        workspaceId: null,
        resourceId,
      },
      { status: 500 },
    );
  }
}

export async function POST(request: NextRequest) {
  let payload: { resource?: string } = {};
  try {
    payload = (await request.json()) as { resource?: string };
  } catch {
    // Empty body → default resource
  }
  const resourceId = payload.resource || "local";
  try {
    const workspaceId = computeWorkspaceId(resourceId);
    const config = await readConfig();
    const existing = config.onboarding_completed_workspaces;
    const table: Record<string, unknown> =
      existing && typeof existing === "object"
        ? { ...(existing as Record<string, unknown>) }
        : {};
    if (table[workspaceId] === true) {
      return NextResponse.json({
        complete: true,
        workspaceId,
        resourceId,
        changed: false,
      });
    }
    table[workspaceId] = true;
    config.onboarding_completed_workspaces = table;
    await writeConfig(config);
    return NextResponse.json({
      complete: true,
      workspaceId,
      resourceId,
      changed: true,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          (error as Error).message ||
          "Failed to mark workspace as onboarded",
      },
      { status: 500 },
    );
  }
}

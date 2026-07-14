import { NextRequest, NextResponse } from "next/server";
import {
  chooseLocalFolder,
  isUserCancelled,
} from "@/app/api/_lib/local-folder-picker";
import {
  listLocalWorkspaces,
  removeLocalWorkspace,
  updateLocalWorkspaceRecord,
  updateLocalResourceWorkspace,
} from "@/app/api/workspace/_lib/workspace";

export const runtime = "nodejs";

export async function GET() {
  try {
    return NextResponse.json(await listLocalWorkspaces());
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "无法读取项目列表。",
      },
      { status: 500 }
    );
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = (await request.json()) as {
      workspacePath?: unknown;
    };
    const workspacePath =
      typeof body.workspacePath === "string" ? body.workspacePath.trim() : "";
    if (!workspacePath) {
      throw new Error("项目路径不能为空。");
    }

    await updateLocalResourceWorkspace(workspacePath);
    return NextResponse.json(await listLocalWorkspaces());
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "无法切换项目。",
      },
      { status: 500 }
    );
  }
}

export async function PATCH(request: NextRequest) {
  try {
    const body = (await request.json()) as {
      workspaceId?: unknown;
      label?: unknown;
      workspacePath?: unknown;
      chooseFolder?: unknown;
      refreshLabel?: unknown;
    };
    const workspaceId =
      typeof body.workspaceId === "string" ? body.workspaceId.trim() : "";
    if (!workspaceId) {
      return NextResponse.json(
        { error: "项目 ID 不能为空。" },
        { status: 400 }
      );
    }

    let workspacePath =
      typeof body.workspacePath === "string" ? body.workspacePath.trim() : "";
    if (body.chooseFolder === true) {
      const selectedPath = await chooseLocalFolder("重新选择项目文件夹");
      if (!selectedPath) {
        return NextResponse.json({ cancelled: true });
      }
      workspacePath = selectedPath;
    }

    const updated = await updateLocalWorkspaceRecord(workspaceId, {
      label: typeof body.label === "string" ? body.label : undefined,
      workspacePath: workspacePath || undefined,
      refreshLabel: body.refreshLabel === true,
    });
    return NextResponse.json(updated);
  } catch (error) {
    if (isUserCancelled(error)) {
      return NextResponse.json({ cancelled: true });
    }

    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "无法更新项目。",
      },
      { status: 500 }
    );
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const workspaceId = request.nextUrl.searchParams.get("id")?.trim() || "";
    if (!workspaceId) {
      return NextResponse.json(
        { error: "项目 ID 不能为空。" },
        { status: 400 }
      );
    }

    return NextResponse.json(await removeLocalWorkspace(workspaceId));
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "无法移除项目。",
      },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    // Allow manual path entry (e.g. headless Linux with no folder picker GUI).
    // If body provides workspacePath, use it directly; else fall back to
    // native folder picker.
    let manualPath = "";
    try {
      const body = (await request.json()) as { workspacePath?: unknown };
      manualPath =
        typeof body.workspacePath === "string" ? body.workspacePath.trim() : "";
    } catch {
      // No body — POST without payload triggers picker (original behavior)
    }

    const selectedPath = manualPath || (await chooseLocalFolder("选择本机项目文件夹"));
    if (!selectedPath) {
      return NextResponse.json({ cancelled: true });
    }

    const updated = await updateLocalResourceWorkspace(selectedPath);
    const localWorkspaces = await listLocalWorkspaces();
    return NextResponse.json({
      ...localWorkspaces,
      workspaceId: updated.workspaceId,
      workspacePath: updated.workspacePath,
    });
  } catch (error) {
    if (isUserCancelled(error)) {
      return NextResponse.json({ cancelled: true });
    }

    return NextResponse.json(
      {
        error:
          error instanceof Error ? error.message : "无法打开本地项目选择器。",
      },
      { status: 500 }
    );
  }
}

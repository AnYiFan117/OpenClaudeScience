import type { UiLanguage } from "@/lib/i18n";

const TOOL_DISPLAY_NAMES: Record<UiLanguage, Record<string, string>> = {
  zh: {
    "general-purpose": "通用科研助手",
    general_purpose: "通用科研助手",
    task: "调用子助手",
    execute: "执行命令",
    write_todos: "更新待办",
    write_todo: "更新待办",
    writetodo: "更新待办",
    writetodos: "更新待办",
    writeTodo: "更新待办",
    writeTodos: "更新待办",
    "write-todos": "更新待办",
    read_file: "读取文件",
    write_file: "写入文件",
    edit_file: "编辑文件",
    ls: "查看目录",
    glob: "搜索文件",
    grep: "搜索文本",
    get_goal: "读取目标",
    create_goal: "创建目标",
    update_goal: "更新目标",
    remote_compute_submit_job: "SSH 远程任务",
    start_async_task: "启动后台任务",
    check_async_task: "检查后台任务",
    update_async_task: "更新后台任务",
    cancel_async_task: "取消后台任务",
    list_async_tasks: "列出后台任务",
  },
  en: {
    "general-purpose": "General research assistant",
    general_purpose: "General research assistant",
    task: "Call sub-agent",
    execute: "Run command",
    write_todos: "Update todos",
    write_todo: "Update todos",
    writetodo: "Update todos",
    writetodos: "Update todos",
    writeTodo: "Update todos",
    writeTodos: "Update todos",
    "write-todos": "Update todos",
    read_file: "Read file",
    write_file: "Write file",
    edit_file: "Edit file",
    ls: "List directory",
    glob: "Search files",
    grep: "Search text",
    get_goal: "Read goal",
    create_goal: "Create goal",
    update_goal: "Update goal",
    remote_compute_submit_job: "SSH remote job",
    start_async_task: "Start background task",
    check_async_task: "Check background task",
    update_async_task: "Update background task",
    cancel_async_task: "Cancel background task",
    list_async_tasks: "List background tasks",
  },
};

function normalizeToolName(name: string): string {
  return name.trim();
}

export function getToolDisplayName(
  name?: string | null,
  language: UiLanguage = "zh"
): string {
  if (!name) return language === "en" ? "Unknown tool" : "未知工具";

  const normalizedName = normalizeToolName(name);
  const displayNames = TOOL_DISPLAY_NAMES[language] || TOOL_DISPLAY_NAMES.zh;
  return (
    displayNames[normalizedName] ??
    displayNames[normalizedName.toLowerCase()] ??
    normalizedName
  );
}

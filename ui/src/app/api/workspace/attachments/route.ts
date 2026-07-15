import crypto from "crypto";
import path from "path";
import { NextRequest, NextResponse } from "next/server";
import {
  assertReadableFilePath,
  getMimeType,
  readWorkspaceRawFile,
  writeWorkspaceRawFile,
} from "../_lib/workspace";
import {
  buildOfficeReadablePreview,
  officePreviewToMarkdown,
} from "../_lib/office-preview";
import type { WorkspaceOfficePreviewKind } from "@/app/types/workspace";

export const runtime = "nodejs";

const MAX_ATTACHMENT_UPLOAD_SIZE = 16 * 1024 * 1024;
const MAX_PDF_EXTRACT_PAGES = 80;
const MAX_PDF_EXTRACT_CHARS = 200_000;
const MAX_OFFICE_MESSAGE_SUMMARY_CHARS = 24_000;
type OfficeAttachmentType = {
  kind: WorkspaceOfficePreviewKind;
  extension: string;
  mimeType: string;
  extensions: string[];
  mimeTypes: string[];
};
type OfficeAttachmentMatch = OfficeAttachmentType & {
  matchedExtension: string;
};
const OFFICE_ATTACHMENT_TYPES: Record<
  WorkspaceOfficePreviewKind,
  OfficeAttachmentType
> = {
  docx: {
    kind: "docx",
    extension: ".docx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    extensions: [".docx", ".doc"],
    mimeTypes: [
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/msword",
    ],
  },
  pptx: {
    kind: "pptx",
    extension: ".pptx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    extensions: [".pptx", ".ppt"],
    mimeTypes: [
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "application/vnd.ms-powerpoint",
    ],
  },
  xlsx: {
    kind: "xlsx",
    extension: ".xlsx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    extensions: [".xlsx", ".xls"],
    mimeTypes: [
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "application/vnd.ms-excel",
    ],
  },
};

function sanitizePathSegment(value: string, fallback: string): string {
  const cleaned = value
    .replace(/[/\\]/g, "-")
    .split("")
    .filter((char) => {
      const code = char.charCodeAt(0);
      return code > 31 && code !== 127;
    })
    .join("")
    .trim()
    .slice(0, 120);
  if (!cleaned || cleaned === "." || cleaned === "..") {
    return fallback;
  }
  return cleaned.startsWith(".") ? `file-${cleaned.slice(1)}` : cleaned;
}

function isPdfFile(file: File, data: Buffer): boolean {
  const name = file.name.toLowerCase();
  const mimeType = file.type.toLowerCase();
  const hasPdfName = name.endsWith(".pdf");
  const hasPdfMime = mimeType === "application/pdf";
  const hasPdfMagic = data.subarray(0, 5).toString("ascii") === "%PDF-";
  return hasPdfMagic && (hasPdfName || hasPdfMime || !mimeType);
}

function getOfficeAttachmentType(
  fileName: string,
  mimeType: string,
  data: Buffer
): OfficeAttachmentMatch | null {
  const name = fileName.toLowerCase();
  const normalizedMimeType = mimeType.toLowerCase();
  const hasZipMagic = data.subarray(0, 2).toString("ascii") === "PK";

  for (const type of Object.values(OFFICE_ATTACHMENT_TYPES)) {
    const matchedExtension = type.extensions.find((extension) =>
      name.endsWith(extension)
    );
    const matchedMimeType = Boolean(
      normalizedMimeType && type.mimeTypes.includes(normalizedMimeType)
    );
    if (!matchedExtension && !matchedMimeType) {
      continue;
    }
    if (
      normalizedMimeType &&
      !type.mimeTypes.includes(normalizedMimeType) &&
      normalizedMimeType !== "application/octet-stream" &&
      normalizedMimeType !== "application/zip"
    ) {
      return null;
    }
    const usesLegacyFormat =
      matchedExtension === ".doc" ||
      matchedExtension === ".xls" ||
      matchedExtension === ".ppt" ||
      normalizedMimeType === "application/msword" ||
      normalizedMimeType === "application/vnd.ms-excel" ||
      normalizedMimeType === "application/vnd.ms-powerpoint";
    if (!hasZipMagic && !usesLegacyFormat) {
      return null;
    }
    return { ...type, matchedExtension: matchedExtension || type.extension };
  }

  return null;
}

function normalizeExtractedText(text: string): string {
  return text
    .replace(/\r/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{4,}/g, "\n\n\n")
    .trim();
}

async function extractPdfText(data: Buffer): Promise<{
  text: string;
  pageCount?: number;
  extractedPageCount?: number;
  truncated: boolean;
  extractionError?: string;
}> {
  try {
    const { PDFParse } = await import("pdf-parse");
    const parser = new PDFParse({ data: new Uint8Array(data) });
    try {
      const result = await parser.getText({ first: MAX_PDF_EXTRACT_PAGES });
      const normalizedText = normalizeExtractedText(result.text || "");
      const text =
        normalizedText.length > MAX_PDF_EXTRACT_CHARS
          ? normalizedText.slice(0, MAX_PDF_EXTRACT_CHARS).trimEnd()
          : normalizedText;

      return {
        text,
        pageCount: result.total,
        extractedPageCount: Math.min(result.total, MAX_PDF_EXTRACT_PAGES),
        truncated:
          result.total > MAX_PDF_EXTRACT_PAGES ||
          normalizedText.length > MAX_PDF_EXTRACT_CHARS,
      };
    } finally {
      await parser.destroy();
    }
  } catch (error) {
    return {
      text: "",
      truncated: false,
      extractionError:
        error instanceof Error ? error.message : "Unable to extract PDF text.",
    };
  }
}

function workspaceFilePath(filePath: string): string {
  return `/${filePath.replace(/^\/+/, "")}`;
}

function workspaceRuntimePath(filePath: string): string {
  return filePath.replace(/^\/+/, "") || ".";
}

function uploadScopeFromThreadId(value: FormDataEntryValue | null): string {
  return sanitizePathSegment(
    typeof value === "string" && value ? value : "draft",
    "draft"
  );
}

function formString(value: FormDataEntryValue | null): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function buildPdfExtractionMarkdown(
  name: string,
  pdfWorkspacePath: string,
  extracted: Awaited<ReturnType<typeof extractPdfText>>
): string {
  const pdfRuntimePath = workspaceRuntimePath(pdfWorkspacePath);
  const lines = [
    `# ${name}`,
    "",
    "This file was generated by 天玄·千枢科学发现平台 during PDF attachment upload.",
    "",
    `Source PDF logical path (file tools): ${pdfWorkspacePath}`,
    `Source PDF shell/script path: ${pdfRuntimePath}`,
    `Pages extracted: ${extracted.extractedPageCount ?? 0}${
      extracted.pageCount ? ` / ${extracted.pageCount}` : ""
    }`,
    `Text limit: ${MAX_PDF_EXTRACT_CHARS} characters`,
    `Truncated: ${extracted.truncated ? "yes" : "no"}`,
  ];

  if (extracted.extractionError) {
    lines.push("", "## Extraction Error", "", extracted.extractionError);
  }

  lines.push("", "## Extracted Text", "");

  if (extracted.text) {
    lines.push(extracted.text);
  } else {
    lines.push(
      "No extractable text was found. Use the source PDF when layout, figures, tables, OCR, or manual inspection is required."
    );
  }

  return `${lines.join("\n")}\n`;
}

function officeMessageSummary(markdown: string): {
  text: string;
  truncated: boolean;
} {
  if (markdown.length <= MAX_OFFICE_MESSAGE_SUMMARY_CHARS) {
    return { text: markdown.trim(), truncated: false };
  }

  return {
    text: `${markdown
      .slice(0, MAX_OFFICE_MESSAGE_SUMMARY_CHARS)
      .trimEnd()}\n\n[Office attachment summary truncated in this message. Use the readable summary file in the workspace for the full extracted preview.]`,
    truncated: true,
  };
}

export async function POST(request: NextRequest) {
  try {
    const form = await request.formData();
    const resourceId = form.get("resourceId");
    const workspaceId = form.get("workspaceId");
    const threadId = form.get("threadId");
    const uploadScope = uploadScopeFromThreadId(threadId);
    const resourceIdValue = formString(resourceId);
    const workspaceIdValue = formString(workspaceId);
    const workspacePath = formString(form.get("workspacePath"));

    if (workspacePath) {
      assertReadableFilePath(workspacePath);
      const rawFile = await readWorkspaceRawFile(
        workspacePath,
        resourceIdValue,
        workspaceIdValue
      );
      const sourceWorkspacePath = workspaceFilePath(rawFile.path);
      const officeType = getOfficeAttachmentType(
        rawFile.name || path.basename(rawFile.path),
        getMimeType(rawFile.path),
        rawFile.data
      );
      if (!officeType) {
        return NextResponse.json(
          {
            error:
              "Only valid DOC, DOCX, XLS, XLSX, PPT, or PPTX workspace attachments are supported here.",
          },
          { status: 400 }
        );
      }

      const uploadId = crypto.randomUUID();
      const summaryWorkspacePath = [
        ".internagents",
        "uploads",
        uploadScope,
        `${uploadId}-${path.basename(
          rawFile.name,
          officeType.matchedExtension
        )}.summary.md`,
      ].join("/");
      const preview = await buildOfficeReadablePreview(
        rawFile.path,
        rawFile.data
      );
      const summaryMarkdown = officePreviewToMarkdown({
        name: rawFile.name || path.basename(rawFile.path),
        sourceWorkspacePath,
        preview,
      });
      const summary = officeMessageSummary(summaryMarkdown);
      const summaryFileData = await writeWorkspaceRawFile(
        summaryWorkspacePath,
        Buffer.from(summaryMarkdown, "utf8"),
        resourceIdValue,
        workspaceIdValue
      );

      return NextResponse.json({
        attachment: {
          name: rawFile.name || path.basename(rawFile.path),
          mimeType: officeType.mimeType,
          size: rawFile.size,
          kind: "file",
          workspacePath: sourceWorkspacePath,
          extractedWorkspacePath: workspaceFilePath(summaryFileData.path),
          extractedTextSize: summaryFileData.size,
          text: summary.text,
          truncated: Boolean(preview.truncated || summary.truncated),
          extractionError: preview.error,
        },
      });
    }

    const file = form.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json(
        { error: "Missing uploaded file." },
        { status: 400 }
      );
    }

    const data = Buffer.from(await file.arrayBuffer());
    if (data.length > MAX_ATTACHMENT_UPLOAD_SIZE) {
      return NextResponse.json(
        { error: "Attachment file is too large to upload." },
        { status: 413 }
      );
    }

    const isPdf = isPdfFile(file, data);
    const officeType = isPdf
      ? null
      : getOfficeAttachmentType(file.name, file.type, data);
    if (!isPdf && !officeType) {
      return NextResponse.json(
        {
          error:
            "Only valid PDF, DOC, DOCX, XLS, XLSX, PPT, or PPTX attachments are supported here.",
        },
        { status: 400 }
      );
    }

    const expectedExtension = isPdf ? ".pdf" : officeType!.matchedExtension;
    const originalName = sanitizePathSegment(
      file.name || `attachment${expectedExtension}`,
      `attachment${expectedExtension}`
    );
    const extension = path.extname(originalName).toLowerCase();
    const filename =
      extension === expectedExtension
        ? originalName
        : `${path.basename(originalName, extension)}${expectedExtension}`;
    const uploadId = crypto.randomUUID();
    const uploadWorkspacePath = [
      ".internagents",
      "uploads",
      uploadScope,
      `${uploadId}-${filename}`,
    ].join("/");

    if (officeType) {
      const summaryWorkspacePath = [
        ".internagents",
        "uploads",
        uploadScope,
        `${uploadId}-${path.basename(
          filename,
          officeType.matchedExtension
        )}.summary.md`,
      ].join("/");
      const fileData = await writeWorkspaceRawFile(
        uploadWorkspacePath,
        data,
        resourceIdValue,
        workspaceIdValue
      );
      const sourceWorkspacePath = workspaceFilePath(fileData.path);
      const preview = await buildOfficeReadablePreview(fileData.path, data);
      const summaryMarkdown = officePreviewToMarkdown({
        name: file.name || fileData.name,
        sourceWorkspacePath,
        preview,
      });
      const summary = officeMessageSummary(summaryMarkdown);
      const summaryFileData = await writeWorkspaceRawFile(
        summaryWorkspacePath,
        Buffer.from(summaryMarkdown, "utf8"),
        resourceIdValue,
        workspaceIdValue
      );

      return NextResponse.json({
        attachment: {
          name: file.name || fileData.name,
          mimeType: officeType.mimeType,
          size: fileData.size,
          kind: "file",
          workspacePath: sourceWorkspacePath,
          extractedWorkspacePath: workspaceFilePath(summaryFileData.path),
          extractedTextSize: summaryFileData.size,
          text: summary.text,
          truncated: Boolean(preview.truncated || summary.truncated),
          extractionError: preview.error,
        },
      });
    }

    const extractedWorkspacePath = [
      ".internagents",
      "uploads",
      uploadScope,
      `${uploadId}-${path.basename(filename, ".pdf")}.extracted.md`,
    ].join("/");

    const [fileData, extracted] = await Promise.all([
      writeWorkspaceRawFile(
        uploadWorkspacePath,
        data,
        resourceIdValue,
        workspaceIdValue
      ),
      extractPdfText(data),
    ]);
    const pdfWorkspacePath = workspaceFilePath(fileData.path);
    const extractedMarkdown = buildPdfExtractionMarkdown(
      file.name || fileData.name,
      pdfWorkspacePath,
      extracted
    );
    const extractedFileData = await writeWorkspaceRawFile(
      extractedWorkspacePath,
      Buffer.from(extractedMarkdown, "utf8"),
      resourceIdValue,
      workspaceIdValue
    );

    return NextResponse.json({
      attachment: {
        name: file.name || fileData.name,
        mimeType: "application/pdf",
        size: fileData.size,
        kind: "pdf",
        workspacePath: pdfWorkspacePath,
        extractedWorkspacePath: workspaceFilePath(extractedFileData.path),
        extractedTextSize: extractedFileData.size,
        pageCount: extracted.pageCount,
        extractedPageCount: extracted.extractedPageCount,
        truncated: extracted.truncated,
        extractionError: extracted.extractionError,
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Unable to upload attachment.",
      },
      { status: 400 }
    );
  }
}

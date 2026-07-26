import { api } from "./api";
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeChunk,
  BatchImportResult,
  BatchImportJobStatus,
  DedupCheckResult,
  ReindexResult,
} from "@/types";

// ---- Knowledge Base CRUD ----

export async function fetchKnowledgeBases(): Promise<KnowledgeBase[]> {
  return api.get("/api/knowledge-base");
}

export async function createKnowledgeBase(
  name: string,
  embeddingModel: string = "qwen3-embedding-8b",
  pipelineId: string = ""
): Promise<KnowledgeBase> {
  const form = new FormData();
  form.append("name", name);
  form.append("embedding_model", embeddingModel);
  if (pipelineId) form.append("pipeline_id", pipelineId);
  return api.post("/api/knowledge-base", form);
}

export async function deleteKnowledgeBase(kbId: string): Promise<void> {
  return api.delete(`/api/knowledge-base/${kbId}`);
}

// ---- Document Management ----

export async function fetchDocuments(kbId: string): Promise<KnowledgeDocument[]> {
  return api.get(`/api/knowledge-base/${kbId}/docs`);
}

/** Chunking configuration for document upload. */
export interface ChunkOptions {
  strategy?: string;      // "FIXED_WINDOW" | "SEMANTIC" | "BLOCK_AWARE"
  chunkSize?: number;     // target characters per chunk, default 800
  overlap?: number;       // character overlap between chunks, default 100
}

export async function uploadDocument(
  kbId: string,
  file: File,
  options: ChunkOptions = {}
): Promise<KnowledgeDocument> {
  const { strategy = "FIXED_WINDOW", chunkSize = 512, overlap = 128 } = options;
  const form = new FormData();
  form.append("file", file);
  form.append("chunk_strategy", strategy);
  form.append("chunk_size", String(chunkSize));
  form.append("overlap", String(overlap));
  return api.post(`/api/knowledge-base/${kbId}/docs/upload`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 600000,  // 10min, embedding batches take time
  });
}

/** URL upload request. */
export interface URLUploadRequest {
  url: string;
  docName?: string;
  scheduleEnabled?: boolean;
  scheduleCron?: string;
}

/** Upload a document from a URL. */
export async function uploadDocumentFromUrl(
  kbId: string,
  req: URLUploadRequest,
): Promise<KnowledgeDocument> {
  return api.post(`/api/knowledge-base/${kbId}/docs/upload-url`, req, {
    timeout: 600000,
  });
}

export async function deleteDocument(docId: string): Promise<void> {
  return api.delete(`/api/knowledge-base/docs/${docId}`);
}

export async function reprocessDocument(
  docId: string,
  pipelineId: string = "",
  options: ChunkOptions = {},
): Promise<{ chunkCount: number; status: string }> {
  const form = new FormData();
  if (pipelineId) form.append("pipeline_id", pipelineId);
  if (options.strategy) form.append("chunk_strategy", options.strategy);
  if (options.chunkSize !== undefined) {
    form.append("chunk_size", String(options.chunkSize));
  }
  if (options.overlap !== undefined) {
    form.append("overlap", String(options.overlap));
  }
  return api.post(`/api/knowledge-base/docs/${docId}/reprocess`, form, {
    timeout: 600000,
  });
}

export async function fetchChunks(docId: string): Promise<KnowledgeChunk[]> {
  return api.get(`/api/knowledge-base/docs/${docId}/chunks`);
}

export async function updateChunkStatus(
  docId: string,
  chunkId: string,
  enabled: boolean,
): Promise<Pick<KnowledgeChunk, "id" | "enabled" | "updateTime">> {
  return api.patch(`/api/knowledge-base/docs/${docId}/chunks/${chunkId}`, {
    enabled,
  });
}

// ---- Batch Import ----

const BATCH_TIMEOUT = 600000; // 10min

/**
 * Upload multiple files in one request with progress tracking.
 * Returns aggregate result: success/failed/skipped_duplicates counts + per-file status.
 */
export async function batchUploadDocuments(
  kbId: string,
  files: File[],
  options: ChunkOptions = {},
): Promise<BatchImportResult> {
  const { strategy = "FIXED_WINDOW", chunkSize = 512, overlap = 128 } = options;
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  form.append("chunk_strategy", strategy);
  form.append("chunk_size", String(chunkSize));
  form.append("overlap", String(overlap));
  return api.post(`/api/knowledge-base/${kbId}/docs/batch-upload`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: BATCH_TIMEOUT,
  });
}

/** Poll batch import job status. */
export async function getBatchImportStatus(
  jobId: string,
): Promise<BatchImportJobStatus> {
  return api.get(`/api/knowledge-base/batch-import/${jobId}`);
}

// ---- Dedup Check ----

/**
 * Check if a file is a duplicate before uploading.
 * Useful for pre-upload validation in the UI.
 */
export async function checkDuplicate(
  kbId: string,
  file: File,
): Promise<DedupCheckResult> {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/api/knowledge-base/${kbId}/docs/check-duplicate`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

// ---- Incremental Re-index ----

/** Check if document changed and re-index only when needed. */
export async function reindexIfChanged(
  docId: string,
): Promise<ReindexResult> {
  return api.post(
    `/api/knowledge-base/docs/${docId}/reindex-if-changed`,
    undefined,
    { timeout: BATCH_TIMEOUT },
  );
}

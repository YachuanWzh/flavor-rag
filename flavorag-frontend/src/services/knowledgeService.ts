import { api } from "./api";
import type { KnowledgeBase, KnowledgeDocument, KnowledgeChunk } from "@/types";

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
  strategy?: string;      // "FIXED_WINDOW" | "SEMANTIC"
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
  pipelineId: string = ""
): Promise<{ chunkCount: number; status: string }> {
  const form = new FormData();
  if (pipelineId) form.append("pipeline_id", pipelineId);
  return api.post(`/api/knowledge-base/docs/${docId}/reprocess`, form, {
    timeout: 600000,
  });
}

export async function fetchChunks(docId: string): Promise<KnowledgeChunk[]> {
  return api.get(`/api/knowledge-base/docs/${docId}/chunks`);
}

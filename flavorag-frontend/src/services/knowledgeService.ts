import { api } from "./api";
import type { KnowledgeBase, KnowledgeDocument, KnowledgeChunk } from "@/types";

// ---- Knowledge Base CRUD ----

export async function fetchKnowledgeBases(): Promise<KnowledgeBase[]> {
  return api.get("/api/knowledge-base");
}

export async function createKnowledgeBase(
  name: string,
  embeddingModel: string = "qwen3-embedding-8b"
): Promise<KnowledgeBase> {
  const form = new FormData();
  form.append("name", name);
  form.append("embedding_model", embeddingModel);
  return api.post("/api/knowledge-base", form);
}

export async function deleteKnowledgeBase(kbId: string): Promise<void> {
  return api.delete(`/api/knowledge-base/${kbId}`);
}

// ---- Document Management ----

export async function fetchDocuments(kbId: string): Promise<KnowledgeDocument[]> {
  return api.get(`/api/knowledge-base/${kbId}/docs`);
}

export async function uploadDocument(
  kbId: string,
  file: File,
  chunkStrategy: string = "FIXED_SIZE"
): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append("file", file);
  form.append("chunk_strategy", chunkStrategy);
  return api.post(`/api/knowledge-base/${kbId}/docs/upload`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 600000,  // 10min, embedding batches take time
  });
}

export async function deleteDocument(docId: string): Promise<void> {
  return api.delete(`/api/knowledge-base/docs/${docId}`);
}

export async function fetchChunks(docId: string): Promise<KnowledgeChunk[]> {
  return api.get(`/api/knowledge-base/docs/${docId}/chunks`);
}

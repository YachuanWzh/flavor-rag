export interface User {
  id: string;
  username: string;
  role: string;
  avatar?: string;
}

export interface Session {
  id: string;
  conversationId: string;
  title: string;
  lastTime: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinkingContent?: string;
  sources?: SourceRef[];
  recommendedQuestions?: string[];
  messageStatus?: "NORMAL" | "INTERRUPTED";
}

export interface SourceRef {
  documentId: string;
  chunkId: string;
  docName: string;
  chunkIndex: number;
  content: string;
  score: number;
}

// ---- Knowledge Base ----

export interface KnowledgeBase {
  id: string;
  name: string;
  embeddingModel: string;
  collectionName: string;
  createTime: string;
}

export interface KnowledgeDocument {
  id: string;
  docName: string;
  fileType: string;
  fileSize: number;
  chunkCount: number;
  status: "running" | "success" | "failed";
  createTime: string;
}

export interface KnowledgeChunk {
  id: string;
  chunkIndex: number;
  content: string;
  charCount: number;
}

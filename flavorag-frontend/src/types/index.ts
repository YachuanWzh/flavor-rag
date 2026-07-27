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

export interface AppliedMapping {
  source: string;
  target: string;
  type: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinkingContent?: string;
  sources?: SourceRef[];
  recommendedQuestions?: string[];
  messageStatus?: "NORMAL" | "INTERRUPTED";
  agentSteps?: AgentStep[];
  ragModes?: RagModes;
  retrievalChannels?: Record<string, RetrievalChannelStatus>;
  appliedMappings?: AppliedMapping[];
  hydeDoc?: string;
  hydeMeta?: { model?: string; durationMs?: number; timedOut?: boolean };
}

export interface SourceRef {
  documentId: string;
  chunkId: string;
  docName: string;
  chunkIndex: number;
  content: string;
  score: number;
  fusionScore?: number | null;
  rerankScore?: number | null;
  channelScores?: Record<string, {
    rank: number;
    rawScore: number;
    weight: number;
    rrfContribution: number;
  }>;
  matchedChannels?: string[];
  blockType?: string;
  pageStart?: number | null;
  pageEnd?: number | null;
  bboxes?: Array<Record<string, number>>;
  assets?: Array<{
    assetId?: string;
    url?: string;
    storageUrl?: string;
    mimeType?: string;
    description?: string;
  }>;
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
  status: "queued" | "running" | "success" | "failed";
  createTime: string;
}

export interface KnowledgeChunk {
  id: string;
  chunkIndex: number;
  content: string;
  charCount: number;
  enabled: number;      // 1 = enabled, 0 = disabled
  tokenCount?: number;  // estimated token count
  blockType?: string;
  pageStart?: number | null;
  pageEnd?: number | null;
  createTime?: string;
  updateTime?: string;
}

export interface RagModes {
  agenticRag: boolean;
  graphRag: boolean;
  neighborExpansion: boolean;
  hyde: boolean;
}

export interface AgentStep {
  tool: string;
  arguments: Record<string, unknown>;
  observation: Record<string, unknown>;
}

export interface RetrievalChannelStatus {
  ok?: boolean;
  count?: number;
  error?: string | null;
  [key: string]: unknown;
}

export interface GraphNode {
  id: string;
  name: string;
  type?: string;
  description?: string;
  documentId?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  description?: string;
}

export interface GraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  disabled?: boolean;
}

export interface RagCapabilities {
  agenticRag: {
    available: boolean;
    defaultEnabled: boolean;
    maxSteps: number;
  };
  graphRag: {
    available: boolean;
    defaultEnabled: boolean;
    status: string;
  };
  hyde: {
    available: boolean;
    defaultEnabled: boolean;
    model: string;
  };
}

// ---- Batch Import ----

export interface BatchFileResult {
  fileName: string;
  status: "success" | "duplicate" | "error";
  docId?: string;
  chunkCount?: number;
  existingDocId?: string;
  error?: string;
}

export interface BatchImportResult {
  jobId: string;
  status: "pending" | "running" | "success" | "partial" | "error";
  total: number;
  success: number;
  failed: number;
  skippedDuplicates: number;
  perFile: BatchFileResult[];
}

export interface BatchImportFileRecord {
  id: string;
  fileName: string;
  fileType: string;
  fileSize: number;
  status: string;
  docId?: string;
  chunkCount: number;
  errorMessage?: string;
}

export interface BatchImportJobStatus {
  jobId: string;
  status: string;
  totalFiles: number;
  completedFiles: number;
  failedFiles: number;
  skippedDuplicates: number;
  errorMessage: string | null;
  createTime: string;
  files: BatchImportFileRecord[];
}

// ---- Dedup ----

export interface DedupCheckResult {
  isDuplicate: boolean;
  existingDocId?: string;
  existingDocName?: string;
}

// ---- Reindex ----

export interface ReindexResult {
  docId: string;
  changed: boolean;
  contentHash: string;
  reason?: string;
  chunkCount?: number;
}

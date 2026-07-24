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
  docName: string;
  chunkIndex: number;
  content: string;
  score: number;
}

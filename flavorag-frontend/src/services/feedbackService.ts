import { api } from "./api";

export interface FeedbackSubmit {
  message_id: string;
  vote: number;  // 1 = thumbs up, -1 = thumbs down
  reason?: string;
  comment?: string;
}

export interface FeedbackInfo {
  id: string;
  vote: number;
  reason?: string;
  comment?: string;
}

/** Submit or update feedback for a message. */
export async function submitFeedback(req: FeedbackSubmit): Promise<FeedbackInfo> {
  return api.post("/api/conversations/feedback", req);
}

/** Get current user's feedback for a message. */
export async function getFeedback(messageId: string): Promise<FeedbackInfo | null> {
  return api.get(`/api/conversations/feedback/${messageId}`);
}

import { api } from "@/services/api";
import type {
  InterviewDifficulty,
  InterviewHistoryData,
  AlgorithmLanguage,
  InterviewMaterials,
  InterviewProfileData,
  InterviewSession,
} from "./types";

export async function getInterviewMaterials(): Promise<InterviewMaterials> {
  return api.get("/api/interviews/materials");
}

export async function uploadInterviewMaterial(
  kind: "resume" | "jd",
  file: File,
): Promise<InterviewMaterials["resume"]> {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/api/interviews/materials/${kind}`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 180_000,
  });
}

export async function saveInterviewJdText(
  text: string,
  title = "粘贴的岗位 JD",
): Promise<InterviewMaterials["jd"]> {
  return api.post("/api/interviews/materials/jd-text", { text, title });
}

export async function deleteInterviewMaterial(
  kind: "resume" | "jd",
): Promise<void> {
  await api.delete(`/api/interviews/materials/${kind}`);
}

export async function startInterview(input: {
  kbId: string | null;
  conversationId: string | null;
  targetRole: string;
  userFocus: string;
  difficulty: InterviewDifficulty;
  questionCount: number;
  algorithmCount: number;
}): Promise<InterviewSession> {
  return api.post("/api/interviews", {
    kb_id: input.kbId,
    conversation_id: input.conversationId,
    target_role: input.targetRole || null,
    user_focus: input.userFocus || null,
    difficulty: input.difficulty,
    question_count: input.questionCount,
    algorithm_count: input.algorithmCount,
  }, { timeout: 180_000 });
}

export async function getInterview(id: string): Promise<InterviewSession> {
  return api.get(`/api/interviews/${id}`);
}

export async function getInterviewHistory(): Promise<InterviewHistoryData> {
  return api.get("/api/interviews/history");
}

export async function clearInterviewHistory(): Promise<{
  cleared: boolean;
  deletedSessions: number;
}> {
  return api.delete("/api/interviews/history");
}

export async function saveInterviewAnswer(
  interviewId: string,
  questionId: string,
  answer: string,
  skipped = false,
  answerLanguage?: AlgorithmLanguage,
): Promise<void> {
  await api.put(`/api/interviews/${interviewId}/answers/${questionId}`, {
    answer,
    skipped,
    answer_language: answerLanguage,
  });
}

export async function submitInterview(id: string): Promise<InterviewSession> {
  return api.post(`/api/interviews/${id}/submit`, undefined, {
    timeout: 180_000,
  });
}

export async function abandonInterview(id: string): Promise<InterviewSession> {
  return api.post(`/api/interviews/${id}/abandon`);
}

export async function getMyInterviewProfile(): Promise<InterviewProfileData> {
  return api.get("/api/interviews/profile/me");
}

export async function getAdminInterviewProfile(
  userId: string,
): Promise<InterviewProfileData> {
  return api.get(`/api/admin/profiles/${userId}/interview`);
}

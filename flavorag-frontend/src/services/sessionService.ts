import { api } from "./api";
import type { Session } from "@/types";

export async function fetchSessions(): Promise<Session[]> {
  const list = await api.get("/api/conversations");
  return list.map((s: any) => ({
    id: s.id,
    conversationId: s.conversationId,
    title: s.title,
    lastTime: s.lastTime,
  }));
}

export async function createSession(
  title: string = "新对话"
): Promise<Session> {
  const form = new FormData();
  form.set("title", title);
  const result = await api.post("/api/conversations", form);
  return {
    id: result.id,
    conversationId: result.conversationId,
    title: result.title,
    lastTime: String(Date.now()),
  };
}

export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/api/conversations/${id}`);
}

export async function renameSession(
  id: string,
  title: string
): Promise<void> {
  const form = new FormData();
  form.set("title", title);
  await api.put(`/api/conversations/${id}`, form);
}

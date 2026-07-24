import { create } from "zustand";
import type { Message, Session, SourceRef } from "@/types";
import { createStreamResponse, type StreamHandlers } from "@/hooks/useStreamResponse";

interface ChatState {
  sessions: Session[];
  currentSessionId: string | null;
  messages: Message[];
  isLoading: boolean;
  isStreaming: boolean;
  deepThinkingEnabled: boolean;
  streamingMessageId: string | null;
  openedSourceMessageId: string | null;

  setSessions: (sessions: Session[]) => void;
  setCurrentSession: (id: string) => void;
  setMessages: (msgs: Message[]) => void;
  setDeepThinking: (enabled: boolean) => void;
  toggleSourcesPanel: (messageId: string) => void;

  sendMessage: (content: string) => Promise<void>;
  cancelGeneration: () => void;
  addSession: (session: Session) => void;
  removeSession: (id: string) => void;
}

let cancelFn: (() => void) | null = null;

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  messages: [],
  isLoading: false,
  isStreaming: false,
  deepThinkingEnabled: false,
  streamingMessageId: null,
  openedSourceMessageId: null,

  setSessions: (sessions) => set({ sessions }),
  setCurrentSession: (id) => {
    set({ currentSessionId: id, messages: [] });
    // Fetch messages for this session
    fetch(`/api/conversations/${id}/messages`, {
      headers: { Authorization: localStorage.getItem("token") || "" },
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.code === "0") {
          set({ messages: data.data.map((m: any) => formatMessage(m)) });
        }
      });
  },
  setMessages: (msgs) => set({ messages: msgs }),
  setDeepThinking: (enabled) => set({ deepThinkingEnabled: enabled }),
  toggleSourcesPanel: (messageId) =>
    set({
      openedSourceMessageId:
        get().openedSourceMessageId === messageId ? null : messageId,
    }),

  sendMessage: async (content) => {
    const state = get();
    if (state.isStreaming || !content.trim()) return;

    const userMsg: Message = {
      id: `user_${Date.now()}`,
      role: "user",
      content: content.trim(),
    };

    const assistantMsg: Message = {
      id: `asst_${Date.now()}`,
      role: "assistant",
      content: "",
    };

    set({
      messages: [...state.messages, userMsg, assistantMsg],
      isLoading: true,
      isStreaming: true,
      streamingMessageId: assistantMsg.id,
    });

    const params = new URLSearchParams({
      question: content.trim(),
      deep_thinking: String(state.deepThinkingEnabled),
    });
    if (state.currentSessionId) {
      params.set("conversation_id", state.currentSessionId);
    }

    const handlers: StreamHandlers = {
      onMeta: (payload) => {
        if (payload.conversationId && !state.currentSessionId) {
          set({ currentSessionId: payload.conversationId });
        }
      },
      onMessage: (payload) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? { ...m, content: m.content + (payload.delta || "") }
              : m
          ),
        }));
      },
      onThinking: (payload) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? { ...m, thinkingContent: (m.thinkingContent || "") + (payload.delta || "") }
              : m
          ),
        }));
      },
      onFinish: (payload) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? { ...m, id: payload.messageId || m.id, sources: payload.sources }
              : m
          ),
          isLoading: false,
          isStreaming: false,
          streamingMessageId: null,
        }));
      },
      onDone: () => {
        set({ isLoading: false, isStreaming: false, streamingMessageId: null });
      },
      onError: (error) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? { ...m, content: m.content || `[错误] ${error.message}` }
              : m
          ),
          isLoading: false,
          isStreaming: false,
          streamingMessageId: null,
        }));
      },
    };

    const { cancel } = createStreamResponse(
      `/api/rag/v3/chat?${params.toString()}`,
      handlers
    );
    cancelFn = cancel;
    await cancel.start();
  },

  cancelGeneration: () => {
    cancelFn?.();
    cancelFn = null;
    set({ isLoading: false, isStreaming: false, streamingMessageId: null });
  },

  addSession: (session) =>
    set((s) => ({ sessions: [session, ...s.sessions] })),

  removeSession: (id) =>
    set((s) => ({
      sessions: s.sessions.filter((sess) => sess.id !== id),
      currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
      messages: s.currentSessionId === id ? [] : s.messages,
    })),
}));

function formatMessage(m: any): Message {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    thinkingContent: m.thinkingContent,
    sources: m.sources,
    messageStatus: m.messageStatus,
  };
}

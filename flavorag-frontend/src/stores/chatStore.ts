import { create } from "zustand";
import type { Message, Session, SourceRef } from "@/types";
import { createStreamResponse, type StreamHandlers } from "@/hooks/useStreamResponse";

interface ChatState {
  sessions: Session[];
  currentSessionId: string | null;
  selectedKbId: string | null;
  messages: Message[];
  isLoading: boolean;
  isStreaming: boolean;
  deepThinkingEnabled: boolean;
  agenticRagEnabled: boolean;
  graphRagEnabled: boolean;
  neighborExpansionEnabled: boolean;
  hydeEnabled: boolean;
  graphRevision: number;
  streamingMessageId: string | null;
  openedSourceMessageId: string | null;
  progressStage: string | null;
  progressMessage: string | null;

  setSessions: (sessions: Session[]) => void;
  setCurrentSession: (id: string) => void;
  setSelectedKbId: (id: string | null) => void;
  setMessages: (msgs: Message[]) => void;
  setDeepThinking: (enabled: boolean) => void;
  setAgenticRag: (enabled: boolean) => void;
  setGraphRag: (enabled: boolean) => void;
  setNeighborExpansion: (enabled: boolean) => void;
  setHyde: (enabled: boolean) => void;
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
  selectedKbId: null,
  messages: [],
  isLoading: false,
  isStreaming: false,
  deepThinkingEnabled: true,
  agenticRagEnabled: false,
  graphRagEnabled: false,
  neighborExpansionEnabled: false,
  hydeEnabled: false,
  graphRevision: 0,
  streamingMessageId: null,
  openedSourceMessageId: null,
  progressStage: null,
  progressMessage: null,

  setSessions: (sessions) => set({ sessions }),
  setSelectedKbId: (id) => set({ selectedKbId: id }),

  setCurrentSession: (id) => {
    set({ currentSessionId: id, messages: [] });
    // Fetch messages for this session
    const token = localStorage.getItem("token") || "";
    fetch(`/api/conversations/${id}/messages`, {
      headers: { Authorization: token ? `Bearer ${token}` : "" },
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
  setAgenticRag: (enabled) => set({ agenticRagEnabled: enabled }),
  setGraphRag: (enabled) => set({ graphRagEnabled: enabled }),
  setNeighborExpansion: (enabled) => set({ neighborExpansionEnabled: enabled }),
  setHyde: (enabled) => set({ hydeEnabled: enabled }),
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
      agentic_rag: String(state.agenticRagEnabled),
      graph_rag: String(state.graphRagEnabled),
      neighbor_expansion: String(state.neighborExpansionEnabled),
      hyde: String(state.hydeEnabled),
    });
    if (state.currentSessionId) {
      params.set("conversation_id", state.currentSessionId);
    }
    if (state.selectedKbId) {
      params.set("kb_id", state.selectedKbId);
    }

    const handlers: StreamHandlers = {
      onProgress: (payload) => {
        set({ progressStage: payload.stage, progressMessage: payload.message });
      },
      onMeta: (payload) => {
        if (payload.conversationId && !state.currentSessionId) {
          set({ currentSessionId: payload.conversationId });
        }
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? {
                  ...m,
                  ragModes: payload.modes,
                  retrievalChannels: payload.channels,
                  appliedMappings: payload.appliedMappings,
                  hydeDoc: payload.hydeDoc || m.hydeDoc,
                  hydeMeta: payload.hydeMeta || m.hydeMeta,
                }
              : m
          ),
        }));
      },
      onAgent: (payload) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === s.streamingMessageId
              ? { ...m, agentSteps: payload.steps }
              : m
          ),
        }));
      },
      onMessage: (payload) => {
        set((s) => ({
          progressStage: null,
          progressMessage: null,
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
              ? {
                  ...m,
                  id: payload.messageId || m.id,
                  sources: payload.sources,
                  recommendedQuestions: payload.recommendedQuestions,
                  ragModes: payload.modes || m.ragModes,
                  retrievalChannels: payload.channels || m.retrievalChannels,
                }
              : m
          ),
          isLoading: false,
          isStreaming: false,
          streamingMessageId: null,
          progressStage: null,
          progressMessage: null,
          graphRevision: s.graphRevision + (state.graphRagEnabled ? 1 : 0),
        }));
      },
      onDone: () => {
        set({ isLoading: false, isStreaming: false, streamingMessageId: null, progressStage: null, progressMessage: null });
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
          progressStage: null,
          progressMessage: null,
        }));
      },
    };

    const { start, cancel } = createStreamResponse(
      `/api/rag/v3/chat?${params.toString()}`,
      handlers
    );
    cancelFn = cancel;
    try {
      await start();
    } catch (err: any) {
      // Belt-and-suspenders: if start() itself throws before onError fires
      const msg = err instanceof Error ? err.message : String(err);
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === s.streamingMessageId
            ? { ...m, content: m.content || `[错误] ${msg}` }
            : m
        ),
        isLoading: false,
        isStreaming: false,
        streamingMessageId: null,
      }));
    }
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
    recommendedQuestions: m.recommendedQuestions,
    messageStatus: m.messageStatus,
    agentSteps: m.agentSteps,
    ragModes: m.ragModes,
    retrievalChannels: m.retrievalChannels,
    appliedMappings: m.appliedMappings,
    hydeDoc: m.hydeDoc,
    hydeMeta: m.hydeMeta,
  };
}

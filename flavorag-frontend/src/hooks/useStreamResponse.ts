import type {
  AgentStep,
  CitationStats,
  RagModes,
  RetrievalChannelStatus,
  SourceRef,
} from "@/types";

export interface StreamHandlers {
  onMeta?: (payload: {
    conversationId: string;
    taskId: string;
    modes?: RagModes;
    channels?: Record<string, RetrievalChannelStatus>;
    appliedMappings?: Array<{ source: string; target: string; type: string }>;
    hydeDoc?: string;
    hydeMeta?: { model?: string; durationMs?: number; timedOut?: boolean };
  }) => void;
  onAgent?: (payload: { steps: AgentStep[] }) => void;
  onMessage?: (payload: { type: string; delta: string }) => void;
  onThinking?: (payload: { type: string; delta: string }) => void;
  onProgress?: (payload: { stage: string; message: string }) => void;
  onFinish?: (payload: {
    messageId: string;
    sources?: SourceRef[];
    recommendedQuestions?: string[];
    modes?: RagModes;
    channels?: Record<string, RetrievalChannelStatus>;
    citationStats?: CitationStats;
  }) => void;
  onDone?: () => void;
  onCancel?: (payload: unknown) => void;
  onError?: (error: Error) => void;
}

export function createStreamResponse(
  url: string,
  handlers: StreamHandlers,
  signal?: AbortSignal
) {
  const controller = new AbortController();
  const mergedSignal = signal ?? controller.signal;

  const start = async () => {
    try {
      const token = localStorage.getItem("token") || "";
      const response = await fetch(url, {
        headers: {
          Accept: "text/event-stream",
          Authorization: token ? `Bearer ${token}` : "",
        },
        signal: mergedSignal,
      });

      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const errBody = await response.text();
          const parsed = JSON.parse(errBody);
          detail = parsed.detail || parsed.message || detail;
        } catch {}
        throw new Error(detail);
      }

      if (!response.body) throw new Error("No response body");
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let eventName = "message";
      let dataLines: string[] = [];

      const dispatch = () => {
        if (dataLines.length === 0) return;
        const raw = dataLines.join("\n");
        let payload: any;
        try {
          payload = JSON.parse(raw);
        } catch {
          payload = raw;
        }

        switch (eventName) {
          case "meta":
            handlers.onMeta?.(payload);
            break;
          case "progress":
            handlers.onProgress?.(payload);
            break;
          case "message":
            if (payload?.type === "think") {
              handlers.onThinking?.(payload);
            } else {
              handlers.onMessage?.(payload);
            }
            break;
          case "agent":
            handlers.onAgent?.(payload);
            break;
          case "finish":
            handlers.onFinish?.(payload);
            break;
          case "done":
            handlers.onDone?.();
            break;
          case "cancel":
            handlers.onCancel?.(payload);
            break;
          case "error":
            handlers.onError?.(
              new Error(payload?.error || "服务暂时不可用，请稍后重试")
            );
            break;
        }
        eventName = "message";
        dataLines = [];
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          dispatch();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line) {
            dispatch();
            continue;
          }
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
            continue;
          }
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
      }
    } catch (err: any) {
      handlers.onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  };

  return { start, cancel: () => controller.abort() };
}

import type {
  AgentStep,
  CitationStats,
  RagModes,
  RetrievalChannelStatus,
  SourceRef,
} from "@/types";

export interface StreamHandlers {
  onConnected?: () => void;
  onMeta?: (payload: {
    conversationId: string;
    taskId: string;
    modes?: RagModes;
    channels?: Record<string, RetrievalChannelStatus>;
    neighborEvidenceCount?: number;
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
    fullAnswer?: string;
    sources?: SourceRef[];
    recommendedQuestions?: string[];
    modes?: RagModes;
    channels?: Record<string, RetrievalChannelStatus>;
    citationStats?: CitationStats;
  }) => void;
  onDone?: () => void;
  onCancel?: (payload: unknown) => void;
  onError?: (error: Error) => void;
  onReconnect?: (attempt: number) => void;
}

const MAX_RETRIES = 3;
const BASE_RETRY_DELAY_MS = 1000;

export function createStreamResponse(
  url: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  body?: Record<string, unknown>
) {
  const controller = new AbortController();
  const mergedSignal = signal ?? controller.signal;
  let retryCount = 0;

  const attemptStream = async (): Promise<void> => {
    const token = localStorage.getItem("token") || "";
    const response = await fetch(url, {
      method: body ? "POST" : "GET",
      headers: {
        Accept: "text/event-stream",
        Authorization: token ? `Bearer ${token}` : "",
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: mergedSignal,
    });

    if (!response.ok) {
      let detail = "服务暂时不可用，请稍后重试。";
      try {
        const errBody = await response.text();
        const parsed = JSON.parse(errBody);
        detail = parsed.message || (response.status < 500 ? parsed.detail : "") || detail;
        if (parsed.errorId) detail += `（错误编号：${parsed.errorId}）`;
      } catch {}
      // HTTP errors are not retryable
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
        case "connected":
          handlers.onConnected?.();
          break;
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
        case "error": {
          const message = payload?.message || "服务暂时不可用，请稍后重试";
          const reference = payload?.errorId
            ? `${message}（错误编号：${payload.errorId}）`
            : message;
          handlers.onError?.(new Error(reference));
          break;
        }
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
  };

  const start = async () => {
    try {
      await attemptStream();
    } catch (err: any) {
      // Do not retry if explicitly aborted
      if (mergedSignal.aborted) return;

      const isNetworkError =
        err instanceof TypeError || // fetch network failure
        err?.name === "AbortError" === false &&
        (err?.message?.includes("Failed to fetch") ||
          err?.message?.includes("NetworkError") ||
          err?.message?.includes("network") ||
          err?.name === "TypeError");

      // Only retry on network-level errors, not HTTP error responses
      if (isNetworkError && retryCount < MAX_RETRIES) {
        retryCount++;
        handlers.onReconnect?.(retryCount);
        const delay = BASE_RETRY_DELAY_MS * Math.pow(2, retryCount - 1);
        await new Promise((resolve) => setTimeout(resolve, delay));
        if (!mergedSignal.aborted) {
          return start();
        }
        return;
      }

      handlers.onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  };

  return { start, cancel: () => controller.abort(), get retryCount() { return retryCount; } };
}

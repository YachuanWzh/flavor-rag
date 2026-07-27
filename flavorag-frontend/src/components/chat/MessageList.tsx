import { useEffect, useRef } from "react";
import type { Message } from "@/types";
import MessageItem from "./MessageItem";

interface Props {
  messages: Message[];
  isStreaming?: boolean;
  streamingMessageId?: string | null;
  progressMessage?: string | null;
  onViewSources?: (sources: NonNullable<Message["sources"]>, highlightIndex?: number) => void;
  onRecommendedQuestion?: (question: string) => void;
}

export default function MessageList({
  messages,
  isStreaming,
  streamingMessageId,
  progressMessage,
  onViewSources,
  onRecommendedQuestion,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
      {messages.map((msg) => (
        <MessageItem
          key={msg.id}
          message={msg}
          isStreaming={isStreaming && msg.id === streamingMessageId}
          progressMessage={msg.id === streamingMessageId ? progressMessage : null}
          onViewSources={onViewSources}
          onRecommendedQuestion={onRecommendedQuestion}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

import { useState } from "react";

interface Props {
  onSend: (text: string) => void;
  onCancel?: () => void;
  isStreaming: boolean;
}

export default function ChatInput({ onSend, onCancel, isStreaming }: Props) {
  const [text, setText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || isStreaming) return;
    onSend(text);
    setText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t p-3 flex gap-2 bg-white"
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入问题..."
        rows={1}
        disabled={isStreaming}
        className="flex-1 resize-none border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
      />
      {isStreaming ? (
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm hover:bg-red-600"
        >
          停止
        </button>
      ) : (
        <button
          type="submit"
          disabled={!text.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          发送
        </button>
      )}
    </form>
  );
}

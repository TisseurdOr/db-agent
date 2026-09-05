import { useRef, useEffect } from "react";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import type { Message } from "../types";

interface Props {
  messages: Message[];
  isStreaming: boolean;
  sessionId: string;
  onSend: (query: string) => void;
  onFeedback: (id: string, rating: "up" | "down") => void;
}

export default function ChatArea({ messages, isStreaming, sessionId, onSend, onFeedback }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="chat-area" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, overflow: "auto", padding: "0 20px" }}>
        {messages.length === 0 && (
          <div style={{
            textAlign: "center", color: "#666", marginTop: "20vh",
            fontSize: 15, lineHeight: 2,
          }}>
            <div style={{ color: "#aaa", fontSize: 18, fontWeight: 600 }}>db-agent · AI data analysis</div>
            <div>Query the database in natural language</div>
            <div style={{ fontSize: 13, color: "#555", marginTop: 16 }}>
              Try: top product sales in East China · average salary by department · orders schema
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            sessionId={sessionId}
            onFeedback={onFeedback}
          />
        ))}
        <div ref={bottomRef} />
      </div>
      <ChatInput onSend={onSend} disabled={isStreaming} />
    </div>
  );
}

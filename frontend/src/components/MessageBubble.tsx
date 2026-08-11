import ThinkingSteps from "./ThinkingSteps";
import AnswerText from "./AnswerText";
import ChartPanel from "./ChartPanel";
import FeedbackButtons from "./FeedbackButtons";
import type { Message } from "../types";

interface Props {
  message: Message;
  sessionId: string;
  onFeedback: (id: string, rating: "up" | "down") => void;
}

export default function MessageBubble({ message, sessionId, onFeedback }: Props) {
  const isUser = message.role === "user";

  return (
    <div
      className={`message ${isUser ? "user" : "assistant"}`}
      style={{
        display: "flex", gap: 12, padding: "12px 0",
        flexDirection: isUser ? "row-reverse" : "row",
      }}
    >
      <div
        style={{
          width: 32, height: 32, borderRadius: "50%",
          background: isUser ? "#4a6cf7" : "#50b050",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 16, flexShrink: 0, color: "#fff",
        }}
      >
        {isUser ? "U" : "A"}
      </div>
      <div
        style={{
          maxWidth: "80%", padding: "12px 16px", borderRadius: 14,
          background: isUser ? "#2a3a6e" : "#1e1e30",
          border: `1px solid ${isUser ? "#3a4f9e" : "#333"}`,
        }}
      >
        {isUser ? (
          <div style={{ whiteSpace: "pre-wrap" }}>{message.content}</div>
        ) : (
          <>
            <ThinkingSteps steps={message.steps} />
            {message.content && (
              <AnswerText text={message.content} isStreaming={message.isStreaming} />
            )}
            {message.isStreaming && !message.content && (
              <span className="cursor-blink">▌</span>
            )}
            <ChartPanel charts={message.charts} />
            {message.sql && (
              <details style={{ marginTop: 12, fontSize: 12 }}>
                <summary style={{ color: "#888", cursor: "pointer" }}>生成的 SQL</summary>
                <pre style={{
                  background: "#111", color: "#aaa", padding: "8px 12px",
                  borderRadius: 6, overflow: "auto", marginTop: 6, fontSize: 12,
                  maxHeight: 200,
                }}>
                  {message.sql}
                </pre>
              </details>
            )}
            {!message.isStreaming && message.content && (
              <FeedbackButtons
                traceId={message.traceId}
                opikTraceId={message.opikTraceId}
                query=""
                answer={message.content}
                sql={message.sql}
                sessionId={sessionId}
                feedbackGiven={message.feedbackGiven}
                onFeedback={(rating) => onFeedback(message.id, rating)}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

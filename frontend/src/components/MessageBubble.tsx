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
    <div className={`message msg-row ${isUser ? "user" : "assistant"}`}>
      <div className={`msg-avatar ${isUser ? "user" : "assistant"}`}>
        {isUser ? "U" : "A"}
      </div>
      <div className={`msg-bubble ${isUser ? "user" : "assistant"}`}>
        {isUser ? (
          <div className="msg-text">{message.content}</div>
        ) : (
          <>
            <ThinkingSteps steps={message.steps} />
            {(message.totalTokens || 0) > 0 || (message.totalElapsed || 0) > 0 ? (
              <div className="msg-meta">
                {(message.totalElapsed || 0) > 0 && (
                  <span>
                    耗时{" "}
                    {(message.totalElapsed || 0) < 1
                      ? `${Math.round((message.totalElapsed || 0) * 1000)}ms`
                      : `${(message.totalElapsed || 0).toFixed(1)}s`}
                  </span>
                )}
                {(message.totalTokens || 0) > 0 && (
                  <span className="msg-meta-gap">Token {message.totalTokens}</span>
                )}
                {message.cancelled && (
                  <span className="msg-meta-gap msg-warn">已取消</span>
                )}
              </div>
            ) : message.cancelled ? (
              <div className="msg-meta msg-warn">已取消</div>
            ) : null}
            {message.content && (
              <AnswerText text={message.content} isStreaming={message.isStreaming} />
            )}
            {message.isStreaming && !message.content && (
              <span className="cursor-blink">▌</span>
            )}
            <ChartPanel charts={message.charts} />
            {message.sql && (
              <details className="msg-sql">
                <summary>生成的 SQL</summary>
                <pre>{message.sql}</pre>
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

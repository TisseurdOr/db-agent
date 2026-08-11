interface Props {
  traceId: string;
  opikTraceId?: string;
  query: string;
  answer: string;
  sql: string;
  sessionId: string;
  feedbackGiven: boolean;
  onFeedback: (rating: "up" | "down") => void;
}

export default function FeedbackButtons({
  traceId,
  opikTraceId,
  query,
  answer,
  sql,
  sessionId,
  feedbackGiven,
  onFeedback,
}: Props) {
  if (feedbackGiven) {
    return (
      <div style={{ fontSize: 12, color: "#888", marginTop: 12 }}>
        ✓ 感谢反馈
      </div>
    );
  }

  const submit = (rating: "up" | "down") => {
    onFeedback(rating);
    const opikId = opikTraceId || traceId;
    fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trace_id: traceId,
        opik_trace_id: opikId,
        session_id: sessionId,
        query,
        answer,
        rating,
        sql,
      }),
    }).catch(() => {});
  };

  return (
    <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
      <button
        onClick={() => submit("up")}
        style={{
          background: "none", border: "1px solid #444", borderRadius: 6,
          padding: "4px 12px", cursor: "pointer", fontSize: 16, color: "#ddd",
        }}
        title="有用"
      >
        👍
      </button>
      <button
        onClick={() => submit("down")}
        style={{
          background: "none", border: "1px solid #444", borderRadius: 6,
          padding: "4px 12px", cursor: "pointer", fontSize: 16, color: "#ddd",
        }}
        title="没用"
      >
        👎
      </button>
    </div>
  );
}

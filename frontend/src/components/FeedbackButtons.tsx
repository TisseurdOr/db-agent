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
    return <div className="fb-thanks">✓ 感谢反馈</div>;
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
    <div className="fb-row">
      <button type="button" className="fb-btn" onClick={() => submit("up")} title="有用">
        👍
      </button>
      <button type="button" className="fb-btn" onClick={() => submit("down")} title="没用">
        👎
      </button>
    </div>
  );
}

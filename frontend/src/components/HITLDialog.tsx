import type { InterruptData } from "../types";

interface Props {
  data: InterruptData;
  onApprove: () => void;
  onReject: () => void;
}

export default function HITLDialog({ data, onApprove, onReject }: Props) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
    >
      <div
        style={{
          background: "#1e1e30", border: "1px solid #444", borderRadius: 14,
          padding: 24, maxWidth: 500, width: "90%",
        }}
      >
        <h3 style={{ margin: "0 0 12px", color: "#f0a030" }}>⚠️ 需要审批</h3>
        <p style={{ color: "#aaa", fontSize: 13, marginBottom: 16 }}>
          {data.type === "confidence_gate"
            ? `SQL 置信度 ${((data.confidence || 0) * 100).toFixed(0)}%，低于阈值 70%，请确认是否继续执行：`
            : data.type === "hitl_approval"
              ? `用户 ${data.user} (${data.role}) 请求执行敏感操作，需要审批：`
              : data.message || "此操作需要管理员审批"}
        </p>
        {data.sql && (
          <pre style={{
            background: "#111", color: "#ddd", padding: "10px 14px",
            borderRadius: 8, fontSize: 12, overflow: "auto", maxHeight: 150,
            marginBottom: 16,
          }}>
            {data.sql}
          </pre>
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            onClick={onReject}
            style={{
              padding: "8px 20px", borderRadius: 8, border: "1px solid #555",
              background: "transparent", color: "#ddd", cursor: "pointer", fontSize: 14,
            }}
          >
            拒绝
          </button>
          <button
            onClick={onApprove}
            style={{
              padding: "8px 20px", borderRadius: 8, border: "none",
              background: "#4a6cf7", color: "#fff", cursor: "pointer", fontSize: 14, fontWeight: 600,
            }}
          >
            批准执行
          </button>
        </div>
      </div>
    </div>
  );
}

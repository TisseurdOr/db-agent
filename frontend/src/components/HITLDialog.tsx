import { useState } from "react";
import type { InterruptData } from "../types";

interface Props {
  data: InterruptData;
  onApprove: (clarified?: string) => void;
  onReject: () => void;
}

export default function HITLDialog({ data, onApprove, onReject }: Props) {
  const [clarified, setClarified] = useState("");
  const isClarify = data.type === "clarify";

  const subtitle = isClarify
    ? data.message || "这个问题比较模糊，请补充信息后继续："
    : data.type === "confidence_gate"
      ? `SQL 置信度 ${((data.confidence || 0) * 100).toFixed(0)}%，低于阈值 70%，请确认是否继续执行：`
      : data.type === "hitl_approval"
        ? `用户 ${data.user} (${data.role}) 请求执行敏感操作，需要审批：`
        : data.message || "此操作需要管理员审批";

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
        <h3 style={{ margin: "0 0 12px", color: isClarify ? "#50b0e0" : "#f0a030" }}>
          {isClarify ? "❓ 需要澄清" : "⚠️ 需要审批"}
        </h3>
        <p style={{ color: "#aaa", fontSize: 13, marginBottom: 16 }}>{subtitle}</p>
        {isClarify && data.questions && (
          <pre style={{
            background: "#111", color: "#ddd", padding: "10px 14px",
            borderRadius: 8, fontSize: 12, overflow: "auto", maxHeight: 150,
            marginBottom: 16, whiteSpace: "pre-wrap",
          }}>
            {data.questions}
          </pre>
        )}
        {data.sql && (
          <pre style={{
            background: "#111", color: "#ddd", padding: "10px 14px",
            borderRadius: 8, fontSize: 12, overflow: "auto", maxHeight: 150,
            marginBottom: 16,
          }}>
            {data.sql}
          </pre>
        )}
        {isClarify && (
          <textarea
            value={clarified}
            onChange={(e) => setClarified(e.target.value)}
            placeholder="输入你的回答，例如：查询华东区上个月的销售额"
            style={{
              width: "100%", boxSizing: "border-box", minHeight: 64,
              background: "#111", color: "#ddd", border: "1px solid #555",
              borderRadius: 8, padding: "10px 14px", fontSize: 13, marginBottom: 16,
              fontFamily: "inherit", resize: "vertical",
            }}
          />
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            onClick={onReject}
            style={{
              padding: "8px 20px", borderRadius: 8, border: "1px solid #555",
              background: "transparent", color: "#ddd", cursor: "pointer", fontSize: 14,
            }}
          >
            {isClarify ? "取消" : "拒绝"}
          </button>
          <button
            onClick={() => (isClarify ? onApprove(clarified) : onApprove())}
            style={{
              padding: "8px 20px", borderRadius: 8, border: "none",
              background: "#4a6cf7", color: "#fff", cursor: "pointer", fontSize: 14, fontWeight: 600,
            }}
          >
            {isClarify ? "提交澄清" : "批准执行"}
          </button>
        </div>
      </div>
    </div>
  );
}

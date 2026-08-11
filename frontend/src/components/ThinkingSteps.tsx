import type { Step } from "../types";

interface Props {
  steps: Step[];
}

const LABELS: Record<string, string> = {
  preprocessing: "护栏检查",
  router: "意图路由",
  clarify: "需求澄清",
  data_quality: "数据质量",
  sql: "SQL 生成",
  hbase: "HBase 查询",
  hive: "Hive 查询",
  strategy: "制度检索",
  analysis: "综合分析",
  confidence_gate: "置信度门控",
  reflection: "质量反思",
  graph: "多Agent执行",
};

const STATUS_ICONS: Record<string, string> = {
  pending: "○",
  running: "◌",
  done: "✓",
  error: "✗",
};

export default function ThinkingSteps({ steps }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  if (steps.length === 0) return null;

  return (
    <div className="thinking-steps">
      <div
        className="steps-header"
        onClick={() => setCollapsed(!collapsed)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "#888", marginBottom: collapsed ? 0 : 8 }}
      >
        <span style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0)", transition: "0.2s" }}>▼</span>
        <span>思考过程 ({steps.length} 步)</span>
      </div>
      {!collapsed && (
        <div className="steps-list" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {steps.map((step, i) => (
            <StepCard key={`${step.node}-${i}`} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}

function StepCard({ step }: { step: Step }) {
  const label = LABELS[step.node] || step.node;
  const icon = STATUS_ICONS[step.status] || "○";
  const color =
    step.status === "running" ? "#f0a030" :
    step.status === "error" ? "#e05050" :
    step.status === "done" ? "#50b050" : "#666";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color }}>
      <span style={{ fontWeight: "bold", minWidth: 16, textAlign: "center" }}>{icon}</span>
      <span style={{ color: "#aaa", minWidth: 72 }}>{label}</span>
      <span style={{ color: "#777", flex: 1 }}>{step.task?.slice(0, 50)}</span>
      {step.elapsed !== undefined && (
        <span style={{ color: "#666", minWidth: 50, textAlign: "right" }}>
          {step.elapsed < 1 ? `${(step.elapsed * 1000).toFixed(0)}ms` : `${step.elapsed.toFixed(1)}s`}
        </span>
      )}
      {step.tokens !== undefined && step.tokens > 0 && (
        <span style={{ color: "#555", minWidth: 60, textAlign: "right" }}>{step.tokens}t</span>
      )}
    </div>
  );
}

import { useState } from "react";

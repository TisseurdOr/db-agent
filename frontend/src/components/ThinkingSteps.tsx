import { useState } from "react";
import type { Step } from "../types";

interface Props {
  steps: Step[];
}

const LABELS: Record<string, string> = {
  connecting: "Connecting",
  init: "Session init",
  preprocessing: "Guardrail check",
  router: "Intent routing",
  clarify: "Clarify",
  data_quality: "Data quality",
  sql: "SQL Agent",
  hbase: "HBase Agent",
  hive: "Hive Agent",
  strategy: "Policy retrieval",
  analysis: "Analysis",
  confidence_gate: "Confidence gate",
  reflection: "Reflection",
  graph: "Multi-agent graph",
  guardrail: "Guardrail block",
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
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#888", marginBottom: collapsed ? 0 : 8 }}
      >
        <span style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0)", transition: "0.2s" }}>▼</span>
        <span>Thinking ({steps.length} steps)</span>
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
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color }}>
      <span style={{ fontWeight: "bold", minWidth: 16, textAlign: "center" }}>{icon}</span>
      <span style={{ color: "#aaa", minWidth: 72 }}>{label}</span>
      <span style={{ color: "#777", flex: 1 }}>{step.task?.slice(0, 50)}</span>
      {step.elapsed !== undefined && (
        <span style={{ color: "#666", minWidth: 50, textAlign: "right" }}>
          {step.elapsed < 1 ? `${(step.elapsed * 1000).toFixed(0)}ms` : `${step.elapsed.toFixed(1)}s`}
        </span>
      )}
      {step.status === "done" && (step.tokens ?? 0) > 0 && (
        <span style={{ color: "#888", minWidth: 60, textAlign: "right" }}>{step.tokens}t</span>
      )}
    </div>
  );
}

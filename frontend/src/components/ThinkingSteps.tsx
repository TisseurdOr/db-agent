import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Step } from "../types";

echarts.use([GraphChart, TooltipComponent, CanvasRenderer]);

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
  const [view, setView] = useState<"list" | "graph">("list");

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
        {!collapsed && steps.length > 1 && (
          <button
            type="button"
            className="steps-view-toggle"
            onClick={(e) => { e.stopPropagation(); setView(view === "list" ? "graph" : "list"); }}
            style={{ marginLeft: "auto", padding: "2px 8px", fontSize: 11, color: "#4a6cf7", background: "none", border: "1px solid #4a6cf7", borderRadius: 4, cursor: "pointer" }}
          >
            {view === "list" ? "Graph" : "List"}
          </button>
        )}
      </div>
      {!collapsed && (
        view === "list" ? (
          <div className="steps-list" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {steps.map((step, i) => (
              <StepCard key={`${step.node}-${i}`} step={step} />
            ))}
          </div>
        ) : (
          <StepGraph steps={steps} />
        )
      )}
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  pending: "#666",
  running: "#f0a030",
  done: "#50b050",
  error: "#e05050",
};

function StepGraph({ steps }: { steps: Step[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = Math.max(steps.length * 150, 560);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: {
        formatter: (p: { dataType?: string; data?: Step }) => {
          const s = p.data as Step;
          return `${LABELS[s.node] || s.node} · ${s.status}${s.elapsed != null ? ` · ${s.elapsed.toFixed(1)}s` : ""}${(s.tokens ?? 0) > 0 ? ` · ${s.tokens}t` : ""}`;
        },
      },
      series: [
        {
          type: "graph",
          layout: "none",
          data: steps.map((s, i) => ({
            id: String(i),
            name: LABELS[s.node] || s.node,
            x: i * 150,
            y: 0,
            symbolSize: 34,
            status: s.status,
            itemStyle: { color: STATUS_COLOR[s.status] || "#666" },
            label: { show: true, position: "bottom", fontSize: 10, color: "#94a3b8" },
          })),
          links: steps.slice(1).map((_, i) => ({ source: String(i), target: String(i + 1) })),
          lineStyle: { color: "#94a3b8", width: 1.5 },
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 6,
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [steps, width]);

  return (
    <div style={{ overflowX: "auto" }}>
      <div ref={ref} style={{ width, height: 120 }} />
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

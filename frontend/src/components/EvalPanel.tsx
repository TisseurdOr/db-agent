/* Eval 结果页 —— 读 /api/eval：回归基线 + 跑分历史。
 *
 * - 基线表：每个 mode 最近一次通过率（logs/eval_baseline.json）
 * - 趋势图：历次通过率曲线（logs/eval_history.jsonl，按 mode 分组）
 * - 明细表：最近几次跑分（通过/总数、模型、judge、失败用例）
 * 图表用 echarts（已在依赖里）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import "./OpsPanel.css";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

interface EvalRecord {
  ts: string;
  mode: string;
  pass_rate: number;
  case_count: number;
  passed: number;
  total: number;
  model: string;
  judge: boolean;
  failed_ids: string[];
}

interface EvalBaseline {
  pass_rate: number;
  case_count: number;
  timestamp: string;
}

interface EvalJob {
  status: "idle" | "running" | "ok" | "error";
  mode?: string | null;
  message?: string;
  pass_rate?: number | null;
  exit_code?: number | null;
}

interface EvalData {
  baseline: Record<string, EvalBaseline>;
  history: EvalRecord[];
  modes: string[];
  job?: EvalJob;
}

interface AblationConfig {
  name: string;
  threshold: number | null;
  "hit@1": number;
  "hit@3": number;
  "hit@5": number;
  "hit@10": number;
  mrr: number;
  block_rate: number | null;
  killed: number | null;
  elapsed: number;
  llm_calls: number;
}

interface AblationData {
  generated_at: string | null;
  n_pos: number;
  n_neg: number;
  threshold?: number;
  configs: AblationConfig[];
}

const REFRESH_MS = 10000;
const MODE_COLORS = ["#4a6cf7", "#50b050", "#f0a030", "#e05050", "#9b59b6", "#1abc9c"];

function useEChart(option: echarts.EChartsCoreOption | null) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (chartRef.current && option) chartRef.current.setOption(option, true);
  }, [option]);

  return ref;
}

function TrendChart({ option }: { option: echarts.EChartsCoreOption | null }) {
  const ref = useEChart(option);
  return <div ref={ref} style={{ width: "100%", height: 260 }} />;
}

function fmtTs(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-US", { hour12: false });
}

function pct(r: number): string {
  return `${(r * 100).toFixed(1)}%`;
}

export default function EvalPanel() {
  const [data, setData] = useState<EvalData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [jobBusy, setJobBusy] = useState(false);
  const [jobMsg, setJobMsg] = useState("");
  const [ablation, setAblation] = useState<AblationData | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/eval");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData((await res.json()) as EvalData);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    fetch("/api/eval/retrieval-ablation")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setAblation(j as AblationData | null))
      .catch(() => setAblation(null));
  }, []);

  useEffect(() => {
    const j = data?.job;
    if (!j) return;
    setJobBusy(j.status === "running");
    if (j.status === "running") setJobMsg(`Running --${j.mode || "fast"}…`);
    else if (j.status === "ok") setJobMsg(j.pass_rate != null ? `Done · pass ${(j.pass_rate * 100).toFixed(1)}%` : "Done");
    else if (j.status === "error") setJobMsg(j.message || "Failed");
  }, [data?.job]);

  const runEval = async (mode: "fast" | "full") => {
    setJobBusy(true);
    setJobMsg(`Starting --${mode}…`);
    try {
      const res = await fetch("/api/eval/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 409) {
        setJobMsg(body.detail?.message || "Already running");
        return;
      }
      if (!res.ok) throw new Error(body.detail?.message || `HTTP ${res.status}`);
      setJobMsg(`Running --${mode}…`);
      // 轮询稍密一点直到结束
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        await load();
        const st = await fetch("/api/eval/run/status").then((r) => r.json()).catch(() => null);
        if (st && st.status !== "running") {
          setJobBusy(false);
          setJobMsg(
            st.status === "ok"
              ? (st.pass_rate != null ? `Done · pass ${(st.pass_rate * 100).toFixed(1)}%` : "Done")
              : (st.message || "Failed"),
          );
          await load();
          return;
        }
      }
      setJobMsg("Still running — refresh later");
    } catch (e) {
      setJobBusy(false);
      setJobMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (error && !data) return <div className="ov-error">Failed to load: {error}</div>;
  if (!data) return <div className="ov-empty">Loading eval results…</div>;

  // ── 趋势图：x=历次跑分时间，y=通过率%，按 mode 分系列 ──
  const uniqTs = Array.from(new Set(data.history.map((r) => r.ts))).sort();
  const modeSet = Array.from(new Set(data.history.map((r) => r.mode))).sort();
  const trendOption: echarts.EChartsCoreOption = {
    backgroundColor: "#ffffff",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#64748b" }, top: 0 },
    grid: { left: 52, right: 16, top: 30, bottom: 24 },
    xAxis: {
      type: "category",
      data: uniqTs.map(fmtTs),
      axisLabel: { color: "#94a3b8", fontSize: 10, rotate: uniqTs.length > 6 ? 30 : 0 },
      axisLine: { lineStyle: { color: "#e2e8f0" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      axisLabel: { color: "#94a3b8", fontSize: 10, formatter: "{value}%" },
      splitLine: { lineStyle: { color: "#f1f5f9" } },
    },
    series: modeSet.map((m, i) => ({
      name: m,
      type: "line" as const,
      smooth: true,
      showSymbol: true,
      connectNulls: false,
      data: uniqTs.map((ts) => {
        const rec = data.history.find((r) => r.ts === ts && r.mode === m);
        return rec ? Math.round(rec.pass_rate * 1000) / 10 : null;
      }),
      itemStyle: { color: MODE_COLORS[i % MODE_COLORS.length] },
      lineStyle: { width: 2, color: MODE_COLORS[i % MODE_COLORS.length] },
    })),
  };

  const baselineRows = data.modes.map((m) => {
    const b = data.baseline[m];
    return (
      <tr key={m}>
        <td>{m}</td>
        <td style={{ color: (b?.pass_rate ?? 0) >= 0.95 ? "#50b050" : b?.pass_rate >= 0.8 ? "#f0a030" : "#e05050" }}>{pct(b?.pass_rate ?? 0)}</td>
        <td>{b?.case_count ?? 0}</td>
        <td>{b?.timestamp ? fmtTs(b.timestamp) : "—"}</td>
      </tr>
    );
  });

  const recent = [...data.history].reverse().slice(0, 12);

  const job = data.job;
  const tone = jobBusy || job?.status === "running" ? "running" : job?.status === "error" ? "err" : job?.status === "ok" ? "ok" : "";

  return (
    <div className="ops-panel">
      <div className="ops-actions">
        <button className="ops-run-btn" disabled={jobBusy} onClick={() => void runEval("fast")}>
          Run eval --fast
        </button>
        <button className="ops-run-btn" disabled={jobBusy} onClick={() => void runEval("full")} title="Calls LLM; slower">
          Run eval --full
        </button>
        <span className={`ops-run-status ${tone}`}>{jobMsg || "Idle · click to generate eval history"}</span>
      </div>
      <div className="ops-kpis">
        <div className="ops-kpi">
          <div className="ops-kpi-value">{data.history.length}</div>
          <div className="ops-kpi-label">Total eval runs</div>
          <div className="ops-kpi-sub">accumulated in eval_history.jsonl</div>
        </div>
        {data.modes.map((m) => (
          <div className="ops-kpi" key={m}>
            <div className="ops-kpi-value" style={{ color: (data.baseline[m]?.pass_rate ?? 0) >= 0.95 ? "#50b050" : "#f0a030" }}>
              {pct(data.baseline[m]?.pass_rate ?? 0)}
            </div>
            <div className="ops-kpi-label">Baseline · {m}</div>
            <div className="ops-kpi-sub">{data.baseline[m]?.case_count ?? 0} cases</div>
          </div>
        ))}
      </div>

      {ablation && ablation.configs.length > 0 && (
        <div className="ops-chart" style={{ marginBottom: 16 }}>
          <div className="ops-chart-title">检索召回率 · 消融（BM25+RRF 混合 vs 向量基线）</div>
          <div className="ops-meta" style={{ marginBottom: 8 }}>
            {ablation.generated_at ? `更新 ${fmtTs(ablation.generated_at)}` : ""} · 正例 {ablation.n_pos} / 负例 {ablation.n_neg}
            {ablation.threshold != null ? ` · 阈值 ${ablation.threshold}` : ""}
          </div>
          <table className="eval-table">
            <thead>
              <tr><th>配置</th><th>hit@1</th><th>hit@3</th><th>hit@5</th><th>hit@10</th><th>MRR</th><th>LLM 调用</th><th>耗时</th></tr>
            </thead>
            <tbody>
              {ablation.configs.map((c) => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <td style={{ color: c["hit@1"] >= 0.85 ? "#50b050" : c["hit@1"] >= 0.75 ? "#f0a030" : "#e05050" }}>{pct(c["hit@1"])}</td>
                  <td>{pct(c["hit@3"])}</td>
                  <td>{pct(c["hit@5"])}</td>
                  <td>{pct(c["hit@10"])}</td>
                  <td>{c.mrr.toFixed(3)}</td>
                  <td>{c.llm_calls}</td>
                  <td>{c.elapsed}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="ops-grid">
        <div className="ops-chart">
          <div className="ops-chart-title">通过率趋势（按 mode）</div>
          {data.history.length === 0 ? (
            <div style={{ color: "#888", fontSize: 13, padding: "40px 8px" }}>
              暂无历史。运行
              <code style={{ color: "#4a6cf7" }}> python -m tests.eval_runner --fast</code>
              {" "}或{" "}
              <code style={{ color: "#4a6cf7" }}>--full</code> 后，趋势会在这里累积。
            </div>
          ) : (
            <TrendChart option={trendOption} />
          )}
        </div>
      </div>

      <div className="ops-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <div className="ops-chart">
          <div className="ops-chart-title">回归基线（每个 mode 最近一次）</div>
          <table className="eval-table">
            <thead><tr><th>mode</th><th>pass rate</th><th>cases</th><th>last run</th></tr></thead>
            <tbody>{baselineRows.length ? baselineRows : <tr><td colSpan={4} style={{ color: "#666" }}>暂无基线</td></tr>}</tbody>
          </table>
        </div>
        <div className="ops-chart">
          <div className="ops-chart-title">最近跑分明细</div>
          {recent.length === 0 ? (
            <div style={{ color: "#888", fontSize: 13, padding: "24px 8px" }}>暂无历史跑分记录。</div>
          ) : (
            <table className="eval-table">
              <thead><tr><th>time</th><th>mode</th><th>result</th><th>model</th><th>judge</th><th>failed</th></tr></thead>
              <tbody>
                {recent.map((r, i) => (
                  <tr key={`${r.ts}-${i}`}>
                    <td>{fmtTs(r.ts).slice(5)}</td>
                    <td>{r.mode}</td>
                    <td style={{ color: r.pass_rate >= 0.95 ? "#50b050" : r.pass_rate >= 0.8 ? "#f0a030" : "#e05050" }}>
                      {r.passed}/{r.total} · {pct(r.pass_rate)}
                    </td>
                    <td>{r.model || "—"}</td>
                    <td>{r.judge ? "yes" : "—"}</td>
                    <td style={{ color: "#e05050" }}>{r.failed_ids.length ? r.failed_ids.join(", ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="ops-meta">
        Eval 由本页按钮或 CLI <code>python -m tests.eval_runner --fast</code> 触发，写入 logs/eval_*.json(l)。
        Ops 无需按钮：每次对话查询自动记入进程计数（重启清零）。· auto-refresh 10s
        {updatedAt && <> · updated {updatedAt.toLocaleTimeString("en-US", { hour12: false })}</>}
      </div>
    </div>
  );
}

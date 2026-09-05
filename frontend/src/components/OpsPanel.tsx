/* Ops metrics 图形化面板 —— 进程内运维指标（重启清零）的时间序列展示。
 *
 * 数据源: GET /api/ops/series
 *   - series: 内存环形缓冲（5s 采样，保留 15 分钟窗口），含增量与速率
 *   - current: 当前累计快照
 * 图表用 echarts（已在依赖里），不引入新包。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import "./OpsPanel.css";

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

interface OpsPoint {
  ts: number;
  dt: number;
  queries_total: number;
  queries_succeeded: number;
  queries_failed: number;
  tokens_total: number;
  elapsed_total: number;
  elapsed_avg: number;
  elapsed_max: number;
  error_rate: number;
  errors_by_node: Record<string, number>;
  d_queries: number;
  d_succeeded: number;
  d_failed: number;
  d_tokens: number;
  d_elapsed: number;
  queries_rate: number;
  tokens_rate: number;
}

interface OpsData {
  series: OpsPoint[];
  current: OpsPoint;
}

const REFRESH_MS = 5000;

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false });
}
function fmtNum(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n));
}
function fmtDur(s: number): string {
  if (!s) return "0s";
  return s < 1 ? `${Math.round(s * 1000)}ms` : `${s.toFixed(1)}s`;
}

/* echarts 实例生命周期 hook */
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

function Chart({ title, option, height = 200 }: { title: string; option: echarts.EChartsCoreOption | null; height?: number }) {
  const ref = useEChart(option);
  return (
    <div className="ops-chart">
      <div className="ops-chart-title">{title}</div>
      <div ref={ref} style={{ width: "100%", height }} />
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: "good" | "bad" | "warn" }) {
  return (
    <div className="ops-kpi">
      <div className={`ops-kpi-value ${tone ?? ""}`}>{value}</div>
      <div className="ops-kpi-label">{label}</div>
      <div className="ops-kpi-sub">{sub}</div>
    </div>
  );
}

function buildLineOption(labels: string[], series: { name: string; data: number[]; color: string }[]): echarts.EChartsCoreOption {
  return {
    backgroundColor: "#ffffff",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#64748b" }, top: 0 },
    grid: { left: 52, right: 16, top: 30, bottom: 24 },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: "#94a3b8", fontSize: 10 },
      axisLine: { lineStyle: { color: "#e2e8f0" } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#f1f5f9" } },
      axisLabel: { color: "#94a3b8", fontSize: 10 },
    },
    series: series.map((s) => ({
      name: s.name,
      type: "line" as const,
      smooth: true,
      showSymbol: false,
      data: s.data,
      itemStyle: { color: s.color },
      lineStyle: { width: 2, color: s.color },
    })),
  };
}

function buildNodeBar(errors: Record<string, number>): echarts.EChartsCoreOption {
  const entries = Object.entries(errors).sort((a, b) => b[1] - a[1]);
  return {
    backgroundColor: "#ffffff",
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 76, right: 16, top: 16, bottom: 24 },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#f1f5f9" } },
      axisLabel: { color: "#94a3b8", fontSize: 10 },
    },
    yAxis: {
      type: "category",
      data: entries.map((e) => e[0]),
      axisLabel: { color: "#64748b", fontSize: 11 },
      axisLine: { lineStyle: { color: "#e2e8f0" } },
    },
    series: [
      {
        type: "bar" as const,
        data: entries.map((e) => e[1]),
        itemStyle: { color: "#e05050" },
        barMaxWidth: 18,
      },
    ],
  };
}

export default function OpsPanel() {
  const [data, setData] = useState<OpsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ops/series");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData((await res.json()) as OpsData);
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

  if (error && !data) return <div className="ov-error">Failed to load: {error}</div>;
  if (!data) return <div className="ov-empty">Loading ops metrics…</div>;

  const cur = data.current;
  const labels = data.series.map((p) => fmtTime(p.ts));

  const rateOption = buildLineOption(labels, [
    { name: "成功/s", data: data.series.map((p) => Math.round((p.d_succeeded / Math.max(p.dt, 0.1)) * 1000) / 1000), color: "#50b050" },
    { name: "失败/s", data: data.series.map((p) => Math.round((p.d_failed / Math.max(p.dt, 0.1)) * 1000) / 1000), color: "#e05050" },
  ]);
  const errorOption = buildLineOption(labels, [
    { name: "错误率", data: data.series.map((p) => p.error_rate), color: "#f0a030" },
  ]);
  const tokenOption = buildLineOption(labels, [
    { name: "token/s", data: data.series.map((p) => p.tokens_rate), color: "#4a6cf7" },
  ]);
  const latencyOption = buildLineOption(labels, [
    { name: "平均耗时(s)", data: data.series.map((p) => p.elapsed_avg), color: "#4a6cf7" },
    { name: "最大耗时(s)", data: data.series.map((p) => p.elapsed_max), color: "#f0a030" },
  ]);

  return (
    <div className="ops-panel">
      <div className="ops-kpis">
        <Kpi label="Queries (this process)" value={String(cur.queries_total)} sub={`${cur.queries_succeeded} ok · ${cur.queries_failed} fail`} />
        <Kpi label="Error rate" value={`${(cur.error_rate * 100).toFixed(1)}%`} sub={cur.error_rate > 0.2 ? "⚠ high" : "normal"} tone={cur.error_rate > 0.2 ? "bad" : "good"} />
        <Kpi label="Tokens" value={fmtNum(cur.tokens_total)} sub="cumulative" />
        <Kpi label="Avg latency" value={fmtDur(cur.elapsed_avg)} sub={`max ${fmtDur(cur.elapsed_max)}`} />
      </div>
      <div className="ops-grid">
        <Chart title="查询速率（成功 / 失败 per sec）" option={rateOption} />
        <Chart title="错误率趋势（累计口径）" option={errorOption} />
        <Chart title="Token 消耗速率" option={tokenOption} />
        <Chart title="耗时（平均 / 最大）" option={latencyOption} />
        <Chart title="按节点错误分布（累计）" option={buildNodeBar(cur.errors_by_node)} height={260} />
      </div>
      <div className="ops-meta">
        自动采集：每次 Web/CLI 查询会 record_query / tokens / errors（进程内，重启清零）。
        采样 5s · 15min 窗口 · 无需按钮
        {updatedAt && <> · updated {updatedAt.toLocaleTimeString("en-US", { hour12: false })}</>}
      </div>
    </div>
  );
}

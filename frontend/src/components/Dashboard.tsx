/* 数据大屏 —— Agent render_chart 产物的实时可视化。
 *
 * 数据源: GET /api/dashboard/latest（结构化 panels，非 iframe HTML）
 * 样式对齐 Ops / Eval：ops-panel KPI + echarts 网格，3s 轮询。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import "./OpsPanel.css";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  TitleComponent,
  CanvasRenderer,
]);

interface DashPanel {
  type: "line" | "bar" | "pie" | string;
  title: string;
  labels: string[];
  values: number[];
}

interface DashData {
  title: string | null;
  panels: DashPanel[];
  panel_count: number;
  updated_at: string | null;
  url: string | null;
}

const REFRESH_MS = 3000;
const COLORS = ["#4a6cf7", "#50b050", "#f0a030", "#e05050", "#9b59b6", "#1abc9c", "#73c0de"];

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

function ChartCard({ title, option, height = 200 }: { title: string; option: echarts.EChartsCoreOption; height?: number }) {
  const ref = useEChart(option);
  return (
    <div className="ops-chart">
      <div className="ops-chart-title">{title}</div>
      <div ref={ref} style={{ width: "100%", height }} />
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="ops-kpi">
      <div className="ops-kpi-value">{value}</div>
      <div className="ops-kpi-label">{label}</div>
      <div className="ops-kpi-sub">{sub}</div>
    </div>
  );
}

function fmtNum(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function buildOption(panel: DashPanel): echarts.EChartsCoreOption {
  const labels = panel.labels || [];
  const values = panel.values || [];
  const type = (panel.type || "bar").toLowerCase();

  if (type === "pie") {
    return {
      backgroundColor: "#ffffff",
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { bottom: 0, textStyle: { color: "#64748b", fontSize: 10 } },
      color: COLORS,
      series: [
        {
          type: "pie",
          radius: ["42%", "68%"],
          center: ["50%", "46%"],
          data: labels.map((name, i) => ({ name, value: values[i] ?? 0 })),
          itemStyle: { borderRadius: 4, borderColor: "#ffffff", borderWidth: 2 },
          label: { color: "#475569", fontSize: 11 },
        },
      ],
    };
  }

  return {
    backgroundColor: "#ffffff",
    tooltip: { trigger: "axis", axisPointer: { type: type === "bar" ? "shadow" : "line" } },
    grid: { left: 48, right: 16, top: 16, bottom: labels.length > 6 ? 48 : 28 },
    color: COLORS,
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: "#94a3b8", fontSize: 10, rotate: labels.length > 6 ? 30 : 0 },
      axisLine: { lineStyle: { color: "#e2e8f0" } },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#f1f5f9" } },
      axisLabel: { color: "#94a3b8", fontSize: 10 },
    },
    series: [
      type === "line"
        ? {
            type: "line" as const,
            data: values,
            smooth: true,
            showSymbol: values.length <= 12,
            areaStyle: { color: "rgba(74,108,247,0.12)" },
            lineStyle: { width: 2, color: COLORS[0] },
            itemStyle: { color: COLORS[0] },
          }
        : {
            type: "bar" as const,
            data: values.map((v, i) => ({
              value: v,
              itemStyle: { color: COLORS[i % COLORS.length], borderRadius: [3, 3, 0, 0] },
            })),
            barMaxWidth: 36,
          },
    ],
  };
}

function panelKpis(panels: DashPanel[]) {
  return panels.slice(0, 4).map((p) => {
    const vals = p.values || [];
    const total = vals.reduce((a, b) => a + b, 0);
    const useAvg = vals.length > 6;
    const display = vals.length ? (useAvg ? total / vals.length : total) : 0;
    return {
      label: (p.title || "面板").slice(0, 18),
      value: fmtNum(display),
      sub: useAvg ? `avg · ${vals.length} pts` : `${vals.length} pts · sum`,
    };
  });
}

export default function Dashboard({ userId }: { userId: string }) {
  const [data, setData] = useState<DashData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const lastStamp = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      const uid = userId || "dba";
      const res = await fetch(
        `/api/dashboard/latest?user_id=${encodeURIComponent(uid)}`,
        { headers: { "X-Agent-User": uid } },
      );
      if (res.status === 403) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail || body;
        throw new Error(detail.message || "当前角色无权查看 Dashboard");
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as DashData;
      setData(json);
      setError(null);
      const stamp = json.updated_at || json.url || "";
      if (stamp && stamp !== lastStamp.current) {
        lastStamp.current = stamp;
        setUpdatedAt(new Date());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [userId]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const kpis = useMemo(() => (data?.panels?.length ? panelKpis(data.panels) : []), [data]);

  if (error && !data) return <div className="ov-error">Failed to load: {error}</div>;
  if (!data) return <div className="ov-empty">Loading dashboard…</div>;

  const empty = !data.panels?.length;

  return (
    <div className="ops-panel">
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button className="ov-refresh" onClick={() => void load()}>
          Refresh
        </button>
        <span style={{ fontSize: 14, fontWeight: 600, color: "#0f172a" }}>
          {data.title || "Data Dashboard"}
        </span>
        {data.panel_count > 0 && (
          <span className="ops-meta">{data.panel_count} panels · live</span>
        )}
      </div>

      {empty ? (
        <div className="ops-chart" style={{ padding: "40px 16px", textAlign: "center" }}>
          <div style={{ color: "#888", fontSize: 13, lineHeight: 1.6 }}>
            还没有生成数据大屏。
            <br />
            用 <strong style={{ color: "#4a6cf7" }}>DBA / 数据分析师</strong> 身份在对话里说
            「分析销售趋势并画图」——Analysis Agent 调用 <code style={{ color: "#4a6cf7" }}>render_chart</code> 后，
            这里会<strong style={{ color: "#0f172a" }}> 实时刷新</strong>出图。
          </div>
        </div>
      ) : (
        <>
          <div className="ops-kpis">
            {kpis.map((k) => (
              <Kpi key={k.label} label={k.label} value={k.value} sub={k.sub} />
            ))}
          </div>
          <div className="ops-grid">
            {data.panels.map((p, i) => (
              <ChartCard
                key={`${p.title}-${i}`}
                title={p.title || `Panel ${i + 1}`}
                option={buildOption(p)}
                height={p.type === "pie" ? 220 : 200}
              />
            ))}
          </div>
        </>
      )}

      <div className="ops-meta">
        Source: render_chart → /api/dashboard/latest · auto-refresh 3s
        {updatedAt && (
          <> · updated {updatedAt.toLocaleTimeString("en-US", { hour12: false })}</>
        )}
        {data.updated_at && <> · generated {data.updated_at}</>}
      </div>
    </div>
  );
}

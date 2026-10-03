import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import "./LineagePanel.css";

echarts.use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer]);

interface LineageNode {
  id: string;
  label: string;
  group: string;
  layer: string;
  source: string;
  count: number;
}

interface LineageEdge {
  source: string;
  target: string;
  type: string;
  kind?: string;
  from?: string;
  to?: string;
  description?: string;
}

interface LineageData {
  nodes: LineageNode[];
  edges: LineageEdge[];
  summary?: {
    nodes: number;
    edges: number;
    sources: string[];
    layers: string[];
  };
}

type SourceFilter = "all" | "demo" | "warehouse";

const GROUPS = ["sql", "hive", "hbase", "ods", "dim", "dwd", "dws", "ads"] as const;
const GROUP_NAME: Record<string, string> = {
  sql: "SQL",
  hive: "Demo Hive",
  hbase: "HBase",
  ods: "ODS",
  dim: "DIM",
  dwd: "DWD",
  dws: "DWS",
  ads: "ADS",
};
const GROUP_COLOR: Record<string, string> = {
  sql: "#5470c6",
  hive: "#e6b84f",
  hbase: "#ee6666",
  ods: "#78909c",
  dim: "#2fb344",
  dwd: "#f59e0b",
  dws: "#8b5cf6",
  ads: "#ec4899",
};
const SOURCE_NAME: Record<string, string> = {
  all: "全部",
  demo: "demo.db",
  warehouse: "warehouse.db",
};

function formatRows(n: number): string {
  return new Intl.NumberFormat("en-US").format(n || 0);
}

function filteredData(data: LineageData, sourceFilter: SourceFilter): LineageData {
  if (sourceFilter === "all") return data;
  const nodes = data.nodes.filter((n) => n.source === sourceFilter);
  const ids = new Set(nodes.map((n) => n.id));
  return {
    ...data,
    nodes,
    edges: data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
  };
}

function buildOption(data: LineageData): echarts.EChartsCoreOption {
  const categories = GROUPS.map((g) => ({
    name: GROUP_NAME[g],
    itemStyle: { color: GROUP_COLOR[g] },
  }));
  const upstreamCount = new Map<string, number>();
  const downstreamCount = new Map<string, number>();
  for (const e of data.edges) {
    downstreamCount.set(e.source, (downstreamCount.get(e.source) ?? 0) + 1);
    upstreamCount.set(e.target, (upstreamCount.get(e.target) ?? 0) + 1);
  }

  return {
    tooltip: {
      formatter: (p: { dataType?: string; data?: LineageNode & LineageEdge }) => {
        if (p.dataType === "edge") {
          const e = p.data as LineageEdge;
          const cols = e.from ? ` (${e.from} → ${e.to})` : "";
          const kind = e.kind === "reference" ? "Reference" : e.kind === "etl" ? "ETL" : "Foreign key";
          const description = e.description ? `<br/>${e.description}` : "";
          return `${e.source} → ${e.target}<br/>${kind}${cols}${description}`;
        }
        const n = p.data as LineageNode;
        return [
          n.label,
          `${SOURCE_NAME[n.source] ?? n.source} · ${GROUP_NAME[n.group] ?? n.group}`,
          `rows: ${formatRows(n.count)}`,
          `upstream: ${upstreamCount.get(n.id) ?? 0} · downstream: ${downstreamCount.get(n.id) ?? 0}`,
        ].join("<br/>");
      },
    },
    legend: {
      data: categories.map((c) => c.name),
      top: 0,
      type: "scroll",
      textStyle: { color: "#64748b", fontSize: 11 },
    },
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        draggable: true,
        categories,
        data: data.nodes.map((n) => ({
          id: n.id,
          name: n.id,
          label: n.label,
          group: n.group,
          layer: n.layer,
          source: n.source,
          count: n.count,
          category: Math.max(0, GROUPS.indexOf(n.group as (typeof GROUPS)[number])),
          symbolSize: 18 + Math.min(34, Math.log10(n.count + 1) * 8),
        })),
        links: data.edges.map((e) => ({
          source: e.source,
          target: e.target,
          type: e.type,
          kind: e.kind ?? e.type,
          from: e.from,
          to: e.to,
          lineStyle: {
            color: e.kind === "reference" ? "#f59e0b" : e.kind === "etl" ? "#38bdf8" : "#94a3b8",
            width: e.kind === "reference" ? 1.5 : e.kind === "etl" ? 1.8 : 1.2,
            type: e.kind === "reference" ? "dotted" : e.kind === "etl" ? "dashed" : "solid",
            curveness: 0.08,
          },
        })),
        label: {
          show: true,
          position: "right",
          fontSize: 10,
          color: "#64748b",
          formatter: (p: { data?: LineageNode }) => (p.data ? p.data.label : ""),
        },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 7,
        emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
        force: { repulsion: 260, edgeLength: 125, gravity: 0.08 },
      },
    ],
  };
}

export default function LineagePanel() {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [data, setData] = useState<LineageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [selected, setSelected] = useState<LineageNode | null>(null);

  useEffect(() => {
    fetch("/api/lineage")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j) => setData(j as LineageData))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!ref.current || !data) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const visible = filteredData(data, sourceFilter);
    chart.setOption(buildOption(visible), true);
    chart.on("click", (params) => {
      if (params.dataType === "node") {
        setSelected(params.data as LineageNode);
      }
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [data, sourceFilter]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.dispatchAction({ type: "downplay", seriesIndex: 0 });
    if (selected) {
      chart.dispatchAction({ type: "highlight", seriesIndex: 0, name: selected.id });
    }
  }, [selected]);

  if (error) return <div className="lp-error">Failed to load: {error}</div>;
  if (!data) return <div className="lp-empty">Loading lineage…</div>;

  const visible = filteredData(data, sourceFilter);
  const upstream = selected ? visible.edges.filter((e) => e.target === selected.id) : [];
  const downstream = selected ? visible.edges.filter((e) => e.source === selected.id) : [];
  const nodeById = new Map(visible.nodes.map((n) => [n.id, n]));

  return (
    <div className="lp-wrap">
      <div className="lp-head">
        <div className="lp-head-main">
          <div>
            <h2 className="lp-title">Data lineage · 表级血缘</h2>
            <div className="lp-sub">
              demo.db 展示 SQL 外键；warehouse.db 展示 Olist 的 ODS → DIM/DWD → DWS → ADS ETL 链路。
              橙色点线表示指标定义参考血缘；拖拽、缩放或点击节点查看上下游。
            </div>
          </div>
          <div className="lp-controls">
            {(["all", "warehouse", "demo"] as SourceFilter[]).map((source) => (
              <button
                key={source}
                type="button"
                className={`lp-source ${sourceFilter === source ? "on" : ""}`}
                onClick={() => {
                  setSourceFilter(source);
                  setSelected(null);
                }}
              >
                {SOURCE_NAME[source]}
                <span>{source === "all" ? data.nodes.length : data.nodes.filter((n) => n.source === source).length}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="lp-layout">
        <div ref={ref} className="lp-canvas" />
        <aside className="lp-detail">
          {!selected ? (
            <div className="lp-detail-empty">
              <b>Select a table</b>
              <span>点击图中的节点查看表信息、上游来源和下游影响。</span>
            </div>
          ) : (
            <>
              <div className="lp-detail-head">
                <div>
                  <div className="lp-detail-kicker">{SOURCE_NAME[selected.source] ?? selected.source}</div>
                  <h3>{selected.label}</h3>
                </div>
                <button type="button" onClick={() => setSelected(null)}>×</button>
              </div>
              <div className="lp-detail-meta">
                <span>{GROUP_NAME[selected.group] ?? selected.group}</span>
                <span>{formatRows(selected.count)} rows</span>
              </div>
              <div className="lp-detail-section">
                <h4>Upstream · 上游</h4>
                {upstream.length === 0 && <p>无</p>}
                {upstream.map((e) => (
                  <button key={`${e.source}-${e.target}`} type="button" onClick={() => setSelected(nodeById.get(e.source) ?? null)}>
                    <b>{e.source}</b>
                    <span>{e.kind === "reference" ? "REF" : e.kind === "etl" ? "ETL" : "FK"}{e.from ? ` · ${e.from} → ${e.to}` : ""}</span>
                    {e.description && <em>{e.description}</em>}
                  </button>
                ))}
              </div>
              <div className="lp-detail-section">
                <h4>Downstream · 下游</h4>
                {downstream.length === 0 && <p>无</p>}
                {downstream.map((e) => (
                  <button key={`${e.source}-${e.target}`} type="button" onClick={() => setSelected(nodeById.get(e.target) ?? null)}>
                    <b>{e.target}</b>
                    <span>{e.kind === "reference" ? "REF" : e.kind === "etl" ? "ETL" : "FK"}{e.from ? ` · ${e.from} → ${e.to}` : ""}</span>
                    {e.description && <em>{e.description}</em>}
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

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
  count: number;
}

interface LineageEdge {
  source: string;
  target: string;
  type: string;
  from?: string;
  to?: string;
}

interface LineageData {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

const GROUPS = ["sql", "hive", "hbase"] as const;
const GROUP_NAME: Record<string, string> = {
  sql: "SQL (SQLite)",
  hive: "Hive (分层)",
  hbase: "HBase (内存)",
};
const GROUP_COLOR: Record<string, string> = {
  sql: "#5470c6",
  hive: "#fac858",
  hbase: "#ee6666",
};

function buildOption(data: LineageData): echarts.EChartsCoreOption {
  const categories = GROUPS.map((g) => ({
    name: GROUP_NAME[g],
    itemStyle: { color: GROUP_COLOR[g] },
  }));
  return {
    tooltip: {
      formatter: (p: { dataType?: string; data?: LineageNode & LineageEdge }) => {
        if (p.dataType === "edge") {
          const e = p.data as LineageEdge;
          const cols = e.from ? ` (${e.from} → ${e.to})` : "";
          return `${e.source} → ${e.target}<br/>${e.type}${cols}`;
        }
        const n = p.data as LineageNode;
        return `${n.label}<br/>${GROUP_NAME[n.group]} · ${n.layer}<br/>rows: ${n.count}`;
      },
    },
    legend: {
      data: categories.map((c) => c.name),
      top: 0,
      textStyle: { color: "#64748b" },
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
          count: n.count,
          category: GROUPS.indexOf(n.group as (typeof GROUPS)[number]),
          symbolSize: 20 + Math.min(30, Math.log10(n.count + 1) * 8),
        })),
        links: data.edges.map((e) => ({
          source: e.source,
          target: e.target,
          type: e.type,
          from: e.from,
          to: e.to,
        })),
        label: {
          show: true,
          position: "right",
          fontSize: 11,
          color: "#94a3b8",
          formatter: (p: { data?: LineageNode }) => (p.data ? p.data.label : ""),
        },
        edgeSymbol: ["none", "arrow"],
        edgeSymbolSize: 8,
        lineStyle: { color: "#94a3b8", width: 1.5, curveness: 0.1 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
        force: { repulsion: 220, edgeLength: 130, gravity: 0.05 },
      },
    ],
  };
}

export default function LineagePanel() {
  const ref = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<LineageData | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    chart.setOption(buildOption(data));
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data]);

  if (error) return <div className="lp-error">Failed to load: {error}</div>;
  if (!data) return <div className="lp-empty">Loading lineage…</div>;

  return (
    <div className="lp-wrap">
      <div className="lp-head">
        <h2 className="lp-title">Data lineage · 表级血缘</h2>
        <div className="lp-sub">
          SQL 外键为真实血缘；Hive 按 ods/dwd/dim 分层展示（demo 无真实 ETL 边）；HBase 为内存模拟表。拖拽/缩放自由浏览。
        </div>
      </div>
      <div ref={ref} className="lp-canvas" />
    </div>
  );
}

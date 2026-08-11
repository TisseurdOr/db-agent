import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { ChartConfig } from "../types";

interface Props {
  charts: ChartConfig[];
}

export default function ChartPanel({ charts }: Props) {
  if (charts.length === 0) return null;

  return (
    <div className="chart-panel" style={{ marginTop: 16 }}>
      {charts.map((chart, i) => (
        <ChartView key={i} config={chart} />
      ))}
    </div>
  );
}

function ChartView({ config }: { config: ChartConfig }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { height: 300 });
    const option = buildOption(config);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [config]);

  return <div ref={ref} style={{ width: "100%", height: 300 }} />;
}

function buildOption(config: ChartConfig): echarts.EChartsOption {
  const { type, title, labels, values } = config;
  const dark = document.documentElement.classList.contains("dark");

  return {
    title: { text: title, textStyle: { fontSize: 14, color: dark ? "#ddd" : "#333" }, left: "center" },
    tooltip: { trigger: "axis" as const },
    grid: { left: 60, right: 30, top: 50, bottom: 40 },
    xAxis: type === "bar"
      ? { type: "category" as const, data: labels, axisLabel: { rotate: labels.length > 8 ? 45 : 0, fontSize: 11 } }
      : undefined,
    yAxis: type === "bar"
      ? { type: "value" as const }
      : undefined,
    series: [{
      type: type as "bar" | "line" | "pie",
      data: type === "bar" || type === "line" ? values : labels.map((name, i) => ({ name, value: values[i] })),
      itemStyle: {
        color: (params: { dataIndex: number }) => {
          const colors = ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4"];
          return colors[params.dataIndex % colors.length];
        },
      },
    }],
  };
}

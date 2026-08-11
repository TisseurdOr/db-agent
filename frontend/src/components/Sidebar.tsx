import DataSourceSelector from "./DataSourceSelector";

interface Props {
  datasource: string;
  onDatasourceChange: (ds: string) => void;
}

export default function Sidebar({ datasource, onDatasourceChange }: Props) {
  return (
    <aside
      style={{
        width: 240, minWidth: 240, borderRight: "1px solid #333",
        padding: 20, display: "flex", flexDirection: "column", gap: 16,
        overflow: "auto",
      }}
    >
      <div>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: "#ddd", margin: "0 0 4px" }}>
          🤖 db-agent
        </h2>
        <p style={{ fontSize: 12, color: "#666", margin: 0 }}>
          AI 数据分析助手
        </p>
      </div>

      <DataSourceSelector datasource={datasource} onChange={onDatasourceChange} />

      <div style={{ fontSize: 12, color: "#555", marginTop: "auto", lineHeight: 1.8 }}>
        <div style={{ color: "#888", marginBottom: 4, textTransform: "uppercase", letterSpacing: 1 }}>
          Model
        </div>
        <div>DeepSeek Chat</div>
      </div>
    </aside>
  );
}

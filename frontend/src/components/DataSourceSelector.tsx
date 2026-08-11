interface Props {
  datasource: string;
  onChange: (ds: string) => void;
}

const SOURCES = [
  { value: "sqlite", label: "📊 Demo 数据库 (SQLite)" },
  { value: "csv", label: "📁 上传 CSV" },
];

export default function DataSourceSelector({ datasource, onChange }: Props) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, color: "#888", marginBottom: 6, textTransform: "uppercase", letterSpacing: 1 }}>
        数据源
      </div>
      {SOURCES.map((s) => (
        <label
          key={s.value}
          style={{
            display: "flex", alignItems: "center", gap: 8, padding: "6px 0",
            cursor: "pointer", fontSize: 13, color: datasource === s.value ? "#4a6cf7" : "#aaa",
          }}
        >
          <input
            type="radio"
            name="datasource"
            value={s.value}
            checked={datasource === s.value}
            onChange={() => onChange(s.value)}
          />
          {s.label}
        </label>
      ))}
    </div>
  );
}

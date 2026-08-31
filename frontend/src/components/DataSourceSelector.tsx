interface Props {
  datasource: string;
  onChange: (ds: string) => void;
}

const SOURCES = [
  { value: "sqlite", label: "📊 Demo database (SQLite)" },
  { value: "csv", label: "📁 Upload CSV" },
];

export default function DataSourceSelector({ datasource, onChange }: Props) {
  return (
    <div className="side-ctl">
      <div className="side-ctl-label">Data source</div>
      {SOURCES.map((s) => (
        <label
          key={s.value}
          className={`side-radio ${datasource === s.value ? "on" : ""}`}
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

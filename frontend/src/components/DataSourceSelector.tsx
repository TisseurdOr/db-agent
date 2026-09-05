import { IconBarChart, IconFolder } from "./NavIcons";

interface Props {
  datasource: string;
  onChange: (ds: string) => void;
}

const SOURCES = [
  { value: "sqlite", label: "Demo database (SQLite)", icon: IconBarChart },
  { value: "csv", label: "Upload CSV", icon: IconFolder },
];

export default function DataSourceSelector({ datasource, onChange }: Props) {
  return (
    <div className="side-ctl">
      {SOURCES.map((s) => {
        const Icon = s.icon;
        return (
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
            <span className="side-radio-icon"><Icon /></span>
            {s.label}
          </label>
        );
      })}
    </div>
  );
}

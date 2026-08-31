interface Props {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}

export default function DqToggle({ enabled, onChange, disabled }: Props) {
  return (
    <div className="side-ctl">
      <div className="side-ctl-label">Data Quality</div>
      <button
        type="button"
        className={`side-toggle ${enabled ? "on" : ""}`}
        disabled={disabled}
        onClick={() => onChange(!enabled)}
        aria-pressed={enabled}
      >
        <span className="side-toggle-track">
          <span className="side-toggle-knob" />
        </span>
        <span>DQ {enabled ? "ON" : "OFF"}</span>
      </button>
      <div className="side-ctl-hint">
        ON: run DataQuality scan before agents (slower)
      </div>
    </div>
  );
}

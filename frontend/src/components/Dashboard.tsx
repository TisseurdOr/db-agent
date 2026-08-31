import { useCallback, useEffect, useState } from "react";

export default function Dashboard() {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/dashboard/latest");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as { url: string | null };
      setUrl(json.url || null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <button className="ov-refresh" onClick={() => void load()} disabled={!!error && !url}>
          Refresh
        </button>
        {url && <span className="main-tabs-updated">{url}</span>}
      </div>
      {error && !url && <div className="ov-error">Failed to load: {error}</div>}
      {!url && !error && (
        <div style={{ color: "#64748b", padding: 40, textAlign: "center" }}>
          还没有生成数据大屏。在对话里让 Agent 分析数据（如「分析一下销售趋势」）即可自动生成。
        </div>
      )}
      {url && (
        <iframe
          src={url}
          title="数据大屏"
          style={{ flex: 1, width: "100%", border: "1px solid #26263d", borderRadius: 8, minHeight: 400 }}
        />
      )}
    </div>
  );
}

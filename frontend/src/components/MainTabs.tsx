import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArchView,
  NodeBars,
  RecentList,
  type LiveActivity,
  type OverviewData,
} from "./Overview";
import Dashboard from "./Dashboard";
import DatabaseBrowser from "./DatabaseBrowser";
import MemoryBrowser from "./MemoryBrowser";
import type { MainTabId } from "./Sidebar";

interface Props {
  tab: MainTabId;
  liveActivity: LiveActivity[];
  queryPulse: number;
  theme?: "dark" | "light";
}

const REFRESH_MS = 10_000;

const TAB_TITLE: Record<MainTabId, string> = {
  arch: "Architecture",
  nodes: "Node stats",
  queries: "Recent queries",
  dashboard: "Dashboard",
  memory: "Memory",
  database: "Database",
};

export default function MainTabs({ tab, liveActivity, queryPulse, theme = "dark" }: Props) {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/overview");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as OverviewData;
      setData(json);
      setError(null);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    timerRef.current = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [load]);

  void queryPulse;

  const liveRunning = liveActivity.some((a) => a.status === "running");
  const showOverviewChrome = tab === "arch" || tab === "nodes" || tab === "queries";

  return (
    <div className="main-tabs">
      <div className="main-tabs-header">
        <div className="main-tabs-title">{tab === "arch" ? "" : TAB_TITLE[tab]}</div>
        <div className="main-tabs-status">
          {liveRunning
            ? <span className="ov-live">● Query running — nodes light up live</span>
            : showOverviewChrome
              ? <span className="main-tabs-updated">Auto-refresh every 10s</span>
              : <span className="main-tabs-updated">Read-only local persistence</span>}
          {showOverviewChrome && lastUpdated && (
            <span className="main-tabs-updated">
              Updated {lastUpdated.toLocaleTimeString("en-US", { hour12: false })}
            </span>
          )}
          {showOverviewChrome && (
            <button className="ov-refresh" onClick={() => void load()} disabled={!!error && !data}>
              Refresh
            </button>
          )}
        </div>
      </div>

      {showOverviewChrome && error && !data && <div className="ov-error">Failed to load: {error}</div>}
      {showOverviewChrome && !data && !error && <div className="ov-empty">Loading…</div>}

      {data && tab === "arch" && (
        <ArchView data={data} liveActivity={liveActivity} theme={theme} updatedAt={lastUpdated} />
      )}
      {data && tab === "nodes" && (
        <div className="main-tab-panel"><NodeBars nodes={data.nodes} /></div>
      )}
      {data && tab === "queries" && (
        <div className="main-tab-panel"><RecentList recent={data.recent} /></div>
      )}
      {tab === "memory" && (
        <div className="main-tab-panel"><MemoryBrowser /></div>
      )}
      {tab === "database" && (
        <div className="main-tab-panel"><DatabaseBrowser /></div>
      )}
      {tab === "dashboard" && (
        <div className="main-tab-panel"><Dashboard /></div>
      )}
    </div>
  );
}

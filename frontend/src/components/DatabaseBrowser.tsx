import { useCallback, useEffect, useMemo, useState } from "react";
import "./DatabaseBrowser.css";

type StoreKind = "sqlite" | "jsonl" | "hbase";

interface StoreSummary {
  id: string;
  label: string;
  kind: StoreKind;
  primary: boolean;
  summary: string;
  exists: boolean;
}

interface TableSummary {
  name: string;
  count: number;
  description: string;
  group?: string;
  column_families?: string[];
}

interface ActiveStore {
  id: string;
  label: string;
  kind: StoreKind;
  primary: boolean;
  summary: string;
  path: string;
  exists: boolean;
  size: number;
  tables: TableSummary[];
  all_tables: string[];
  groups?: Record<string, TableSummary[]>;
  hbase_tables?: TableSummary[];
  files?: string[];
}

interface TableDetail {
  store: string;
  name: string;
  description: string;
  count: number;
  columns: string[];
  types: Record<string, string>;
  sample: Record<string, unknown>[];
  limit: number;
  column_families?: string[];
}

interface QueryResult {
  ok: boolean;
  error?: string;
  columns: string[];
  rows: unknown[][];
  truncated?: boolean;
}

type EngineTab = "overview" | "sql" | "hive" | "hbase" | "meta" | "query" | string;

const GROUP_LABEL: Record<string, string> = {
  sql: "SQL",
  hive: "Hive",
  hbase: "HBase",
  meta: "Meta",
};

const QUERY_EXAMPLES: Record<string, string[]> = {
  demo: [
    "SELECT dept_name, COUNT(*) AS n FROM employees e JOIN departments d ON e.dept_id = d.dept_id GROUP BY dept_name",
    "SELECT * FROM ods_orders_hive ORDER BY order_date DESC LIMIT 20",
    "SELECT memory_type, content FROM user_memory ORDER BY id DESC LIMIT 20",
    "SELECT rating, query FROM user_feedback ORDER BY id DESC LIMIT 20",
  ],
  agent_state: [
    "SELECT thread_id, checkpoint_id, checkpoint_ns FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 20",
  ],
  metric_registry: [
    "SELECT metric_name, domain, usage_count FROM metric_registry ORDER BY usage_count DESC LIMIT 20",
  ],
};

function fmtSize(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function cellStr(v: unknown): string {
  if (v == null) return "";
  const s = String(v);
  return s.length > 120 ? `${s.slice(0, 120)}…` : s;
}

export default function DatabaseBrowser() {
  const [storeId, setStoreId] = useState("demo");
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [active, setActive] = useState<ActiveStore | null>(null);
  const [engine, setEngine] = useState<EngineTab>("overview");
  const [tableName, setTableName] = useState<string | null>(null);
  const [table, setTable] = useState<TableDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [sql, setSql] = useState(QUERY_EXAMPLES.demo[0]);
  const [qResult, setQResult] = useState<QueryResult | null>(null);
  const [qRunning, setQRunning] = useState(false);

  const loadOverview = useCallback(async (sid: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/database?store=${encodeURIComponent(sid)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json() as { stores: StoreSummary[]; active: ActiveStore };
      setStores(json.stores);
      setActive(json.active);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setActive(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const resolveStoreForTable = useCallback((sid: string, eng: EngineTab) => {
    if (sid === "demo" && eng === "hbase") return "hbase";
    if (sid === "hbase") return "hbase";
    return sid;
  }, []);

  const loadTable = useCallback(async (sid: string, eng: EngineTab, name: string) => {
    setLoading(true);
    setError(null);
    try {
      const apiStore = resolveStoreForTable(sid, eng);
      const res = await fetch(
        `/api/database/table/${encodeURIComponent(name)}?store=${encodeURIComponent(apiStore)}&limit=80`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTable((await res.json()) as TableDetail);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setTable(null);
    } finally {
      setLoading(false);
    }
  }, [resolveStoreForTable]);

  useEffect(() => {
    void loadOverview(storeId);
  }, [storeId, loadOverview]);

  useEffect(() => {
    if (!tableName || engine === "overview" || engine === "query") {
      setTable(null);
      return;
    }
    void loadTable(storeId, engine, tableName);
  }, [tableName, engine, storeId, loadTable]);

  const switchStore = (sid: string) => {
    setStoreId(sid);
    setEngine("overview");
    setTableName(null);
    setQResult(null);
    setSql((QUERY_EXAMPLES[sid] ?? QUERY_EXAMPLES.demo)[0]);
  };

  const tablesForEngine = useMemo(() => {
    if (!active) return [] as TableSummary[];
    if (storeId === "demo") {
      if (engine === "hbase") return active.hbase_tables ?? [];
      if (engine === "sql" || engine === "hive" || engine === "meta") {
        return active.groups?.[engine] ?? [];
      }
      return active.tables ?? [];
    }
    if (storeId === "hbase") return active.tables ?? [];
    return active.tables ?? [];
  }, [active, storeId, engine]);

  const engineTabs = useMemo(() => {
    if (storeId === "demo") {
      return [
        { id: "overview" as EngineTab, label: "Overview" },
        { id: "sql", label: "SQL", n: active?.groups?.sql?.length },
        { id: "hive", label: "Hive", n: active?.groups?.hive?.length },
        { id: "hbase", label: "HBase", n: active?.hbase_tables?.length },
        { id: "meta", label: "Meta", n: active?.groups?.meta?.length },
        { id: "query", label: "SQL console" },
      ];
    }
    if (storeId === "hbase") {
      return [
        { id: "overview" as EngineTab, label: "Overview" },
        ...(active?.tables ?? []).map((t) => ({ id: t.name as EngineTab, label: t.name, n: t.count })),
      ];
    }
    const tabs: { id: EngineTab; label: string; n?: number }[] = [
      { id: "overview", label: "Overview" },
      ...(active?.tables ?? []).map((t) => ({ id: t.name as EngineTab, label: t.name, n: t.count })),
    ];
    if (active?.kind === "sqlite") tabs.push({ id: "query", label: "SQL console" });
    return tabs;
  }, [storeId, active]);

  const openEngine = (id: EngineTab) => {
    setEngine(id);
    setTableName(null);
    setTable(null);
    // 非分组 store：点表名直接打开
    if (storeId !== "demo" && id !== "overview" && id !== "query") {
      setTableName(id);
    }
  };

  const runQuery = async () => {
    setQRunning(true);
    setQResult(null);
    try {
      const res = await fetch("/api/database/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: storeId === "hbase" ? "demo" : storeId, sql }),
      });
      setQResult((await res.json()) as QueryResult);
    } catch (e) {
      setQResult({ ok: false, error: e instanceof Error ? e.message : String(e), columns: [], rows: [] });
    } finally {
      setQRunning(false);
    }
  };

  const showGroupedTables = storeId === "demo" && (engine === "sql" || engine === "hive" || engine === "hbase" || engine === "meta");

  return (
    <div className="db-wrap">
      <div className="db-head">
        <div>
          <h2 className="db-title">Database — everything db-agent stores</h2>
          <div className="db-sub">
            demo split by engine: <b>SQL / Hive / HBase</b>; uploads.db appears after a CSV upload
          </div>
        </div>
        <button type="button" className="db-refresh" onClick={() => void loadOverview(storeId)}>Refresh</button>
      </div>

      <div className="db-stores">
        {stores.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`db-store-chip ${storeId === s.id ? "on" : ""}`}
            onClick={() => switchStore(s.id)}
            title={s.summary}
          >
            {s.label}
            {s.primary && <span className="db-pill">primary</span>}
          </button>
        ))}
      </div>

      <div className="db-subtabs">
        {engineTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`db-subtab ${engine === t.id ? "on" : ""}`}
            onClick={() => openEngine(t.id)}
          >
            {t.label}
            {t.n != null && <span className="db-n">{t.n}</span>}
          </button>
        ))}
      </div>

      {error && <div className="db-error">{error}</div>}
      {loading && !active && <div className="db-empty">Loading…</div>}

      {!loading && active && engine === "overview" && (
        <div className="db-panel">
          <div className="db-card db-card-accent">
            <b>Three-engine demo data.</b>
            <p>
              <code>demo.db</code> holds <b>SQL</b> relational tables and <b>Hive</b> layered tables; <b>HBase</b> is an in-process KV simulator (not a SQLite file). Meta covers RBAC / memory / feedback.
            </p>
          </div>
          <div className="db-card">
            <div className="db-path">{active.path}</div>
            <div className="db-meta">
              {active.exists
                ? `${fmtSize(active.size)} · ${active.kind} · ${active.summary}`
                : active.summary}
            </div>
          </div>

          {storeId === "demo" && active.groups && (
            <>
              <h3 className="db-h">Engines</h3>
              <div className="db-engine-grid">
                {(["sql", "hive", "hbase", "meta"] as const).map((g) => {
                  const items = g === "hbase" ? (active.hbase_tables ?? []) : (active.groups?.[g] ?? []);
                  const rows = items.reduce((s, t) => s + (t.count || 0), 0);
                  return (
                    <button key={g} type="button" className="db-engine-card" onClick={() => openEngine(g)}>
                      <div className="db-engine-name">{GROUP_LABEL[g]}</div>
                      <div className="db-engine-stat">{items.length} tables · {rows} rows</div>
                      <div className="db-meta">{items.map((t) => t.name).join(", ") || "—"}</div>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {storeId !== "demo" && (
            <>
              <h3 className="db-h">Tables</h3>
              <div className="db-scrolly">
                <table className="db-table">
                  <thead>
                    <tr>
                      <th className="dbcol">table</th>
                      <th className="dbcol">rows</th>
                      <th className="dbcol">what it holds</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(active.tables ?? []).map((t) => (
                      <tr key={t.name} className="db-row-click" onClick={() => { setEngine(t.name); setTableName(t.name); }}>
                        <td><code>{t.name}</code></td>
                        <td className="db-meta">{t.count}</td>
                        <td className="db-meta">{t.description || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {showGroupedTables && !tableName && (
        <div className="db-panel">
          <div className="db-meta" style={{ marginBottom: 8 }}>
            {GROUP_LABEL[engine] || engine} · click a table to browse rows
            {engine === "hbase" && " (in-memory KV; columns are cf:qualifier)"}
          </div>
          <div className="db-scrolly">
            <table className="db-table">
              <thead>
                <tr>
                  <th className="dbcol">table</th>
                  <th className="dbcol">rows</th>
                  <th className="dbcol">what it holds</th>
                </tr>
              </thead>
              <tbody>
                {tablesForEngine.map((t) => (
                  <tr key={t.name} className="db-row-click" onClick={() => setTableName(t.name)}>
                    <td><code>{t.name}</code></td>
                    <td className="db-meta">{t.count}</td>
                    <td className="db-meta">{t.description || "—"}</td>
                  </tr>
                ))}
                {tablesForEngine.length === 0 && (
                  <tr><td colSpan={3} className="db-empty">no tables</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {table && tableName && engine !== "overview" && engine !== "query" && (
        <div className="db-panel">
          <div className="db-meta" style={{ marginBottom: 10 }}>
            <button type="button" className="db-qexample" onClick={() => setTableName(null)}>← back</button>
            {" "}{table.description}
            {table.column_families && (
              <> · CF: {table.column_families.join(", ")}</>
            )}
          </div>
          {table.sample.length === 0 ? (
            <div className="db-empty">empty — no rows yet</div>
          ) : (
            <>
              <div className="db-scrolly">
                <table className="db-table">
                  <thead>
                    <tr>
                      {table.columns.map((c) => (
                        <th key={c} className="dbcol">
                          {c}
                          {table.types[c] && <small>{table.types[c].toLowerCase()}</small>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {table.sample.map((row, i) => (
                      <tr key={i}>
                        {table.columns.map((c) => (
                          <td key={c} className="dbcell" title={String(row[c] ?? "")}>{cellStr(row[c])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="db-meta" style={{ marginTop: 6 }}>
                showing {table.sample.length} of {table.count} rows
              </div>
            </>
          )}
        </div>
      )}

      {engine === "query" && (
        <div className="db-panel">
          <div className="db-meta" style={{ marginBottom: 10 }}>
            Read-only SQL (mode=ro). Hive tables live in <code>demo.db</code> — SELECT them directly.
          </div>
          <textarea className="db-sqlbox" value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} rows={5} />
          <div className="db-sql-actions">
            <button type="button" className="db-run" onClick={() => void runQuery()} disabled={qRunning}>
              {qRunning ? "running…" : "Run"}
            </button>
            <span className="db-meta">
              try:{(QUERY_EXAMPLES[storeId] ?? QUERY_EXAMPLES.demo).map((q) => (
                <button key={q} type="button" className="db-qexample" onClick={() => setSql(q)}>
                  {q.length > 52 ? `${q.slice(0, 52)}…` : q}
                </button>
              ))}
            </span>
          </div>
          {qResult && !qResult.ok && <div className="db-error" style={{ marginTop: 10 }}>{qResult.error}</div>}
          {qResult?.ok && qResult.rows.length === 0 && <div className="db-empty" style={{ marginTop: 10 }}>0 rows</div>}
          {qResult?.ok && qResult.rows.length > 0 && (
            <>
              <div className="db-scrolly" style={{ marginTop: 10 }}>
                <table className="db-table">
                  <thead>
                    <tr>{qResult.columns.map((c) => <th key={c} className="dbcol">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {qResult.rows.map((row, i) => (
                      <tr key={i}>{row.map((v, j) => <td key={j} className="dbcell">{cellStr(v)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="db-meta" style={{ marginTop: 6 }}>
                {qResult.rows.length} row(s){qResult.truncated ? " · truncated" : ""}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

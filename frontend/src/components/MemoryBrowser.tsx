import { useCallback, useEffect, useState } from "react";
import "./DatabaseBrowser.css";

interface Pillar {
  id: string;
  title: string;
  count_label: string;
  description: string;
  arch_node?: string;
  arch_group?: string;
  arch_note?: string;
}

interface ArchLegend {
  title: string;
  boxes: { arch: string; pillars: string[] }[];
}

interface FeedbackArch {
  arch_node: string;
  arch_group: string;
  arch_note: string;
}

interface MemoryFact {
  id: number;
  user_id: string;
  memory_type: string;
  content: string;
  created_at: string;
  access_count: number;
}

interface FeedbackRow {
  id: number;
  rating: string;
  query: string;
  comment: string;
  created_at: string;
  trace_id: string;
}

interface MemoryData {
  pillars: Pillar[];
  user_memory: MemoryFact[];
  feedback: FeedbackRow[];
  chroma: { name: string; count: number | null }[];
  sessions: {
    session_count: number;
    message_count: number;
    sessions: { id: string; messages: number; preview?: { role: string; content: string; trace_id?: string }[] }[];
    live?: {
      id: string;
      recent_msgs: number;
      compressed_msgs: number;
      summary: string;
      messages: { role: string; content: string }[];
    }[];
  };
  metric_count: number;
  sql_examples_count: number | null;
  conversations_count: number | null;
  recent_traces: { trace_id: string; query: string; started_at: string; elapsed: number }[];
  paths: Record<string, string>;
  arch_legend?: ArchLegend;
  feedback_arch?: FeedbackArch;
  checkpoint_arch?: FeedbackArch;
}

type Sub = "overview" | "semantic" | "vector" | "procedural" | "short" | "feedback" | "checkpoint";

export default function MemoryBrowser() {
  const [data, setData] = useState<MemoryData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sub, setSub] = useState<Sub>("overview");
  const [loading, setLoading] = useState(true);
  const [cpData, setCpData] = useState<{
    arch_node: string;
    arch_note: string;
    path: string;
    sqlite_count: number;
    checkpoints: { thread_id: string; checkpoint_id: string; parent_checkpoint_id?: string; type?: string; metadata?: string }[];
    live: { session_id: string; thread_id: string; keys: string[]; next?: string | null; summary: string }[];
  } | null>(null);
  const [cpError, setCpError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/memory");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCheckpoints = useCallback(async () => {
    try {
      const res = await fetch("/api/memory/checkpoints");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCpData(await res.json());
      setCpError(null);
    } catch (e) {
      setCpError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (sub === "checkpoint") void loadCheckpoints();
  }, [sub, loadCheckpoints]);

  const pillarById = (id: string) => data?.pillars.find((p) => p.id === id);

  const tabs: { id: Sub; label: string; n?: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "semantic", label: "Semantic", n: data ? String(data.user_memory.length) : undefined },
    { id: "vector", label: "Vector", n: data ? String(data.chroma.length) : undefined },
    { id: "procedural", label: "Procedural" },
    { id: "short", label: "Short-term" },
    { id: "feedback", label: "Feedback", n: data ? String(data.feedback.length) : undefined },
    { id: "checkpoint", label: "Checkpoint" },
  ];

  return (
    <div className="db-wrap">
      <div className="db-head">
        <div>
          <h2 className="db-title">Memory — what the agent remembers</h2>
          <div className="db-sub">
            Curated view of short / semantic / vector / procedural memory. Same facts appear as raw tables in Database.
          </div>
        </div>
        <button type="button" className="db-refresh" onClick={() => void load()}>Refresh</button>
      </div>

      <div className="db-subtabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`db-subtab ${sub === t.id ? "on" : ""}`}
            onClick={() => setSub(t.id)}
          >
            {t.label}
            {t.n != null && <span className="db-n">{t.n}</span>}
          </button>
        ))}
      </div>

      {error && <div className="db-error">{error}</div>}
      {loading && !data && <div className="db-empty">Loading…</div>}

      {data && sub === "overview" && (
        <div className="db-panel">
          <div className="db-card db-card-accent">
            <b>Same stores, two cuts.</b>
            <p>
              Architecture draws three MEMORY boxes (Short-term / Long-term / Vector store).
              This page uses Waku-style pillars — Long-term is split into Semantic + Procedural.
              Each card shows which Architecture node it maps to.
            </p>
          </div>

          {data.arch_legend && (
            <>
              <h3 className="db-h">{data.arch_legend.title}</h3>
              <div className="db-engine-grid" style={{ marginBottom: 14 }}>
                {data.arch_legend.boxes.map((b) => (
                  <div key={b.arch} className="db-engine-card" style={{ cursor: "default" }}>
                    <div className="db-engine-name">Arch · {b.arch}</div>
                    <div className="db-meta" style={{ marginTop: 6 }}>→ {b.pillars.join(" + ")}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          <h3 className="db-h">The pillars</h3>
          <div className="db-engine-grid">
            {data.pillars.map((p) => (
              <button
                key={p.id}
                type="button"
                className="db-engine-card"
                onClick={() => setSub((p.id === "semantic" || p.id === "vector" || p.id === "procedural" || p.id === "short") ? p.id : "overview")}
              >
                <div className="db-engine-card-head">
                  <div className="db-engine-name">{p.title}</div>
                  {p.arch_node && <span className="db-arch-badge">Arch · {p.arch_node}</span>}
                </div>
                <div className="db-engine-stat">{p.count_label}</div>
                <div className="db-meta">{p.description}</div>
                {p.arch_note && <div className="db-arch-note">{p.arch_note}</div>}
              </button>
            ))}
            {data.feedback_arch && (
              <button type="button" className="db-engine-card" onClick={() => setSub("feedback")}>
                <div className="db-engine-card-head">
                  <div className="db-engine-name">Feedback</div>
                  <span className="db-arch-badge">Arch · {data.feedback_arch.arch_node}</span>
                </div>
                <div className="db-engine-stat">{data.feedback.length} ratings</div>
                <div className="db-meta">Thumbs from Chat — learning loop into Long-term / few-shot.</div>
                <div className="db-arch-note">{data.feedback_arch.arch_note}</div>
              </button>
            )}
            {(data.checkpoint_arch || true) && (
              <button type="button" className="db-engine-card" onClick={() => setSub("checkpoint")}>
                <div className="db-engine-card-head">
                  <div className="db-engine-name">Checkpoint</div>
                  <span className="db-arch-badge">Arch · {(data.checkpoint_arch?.arch_node) || "Short-term (session thread)"}</span>
                </div>
                <div className="db-engine-stat">LangGraph state</div>
                <div className="db-meta">Thread snapshots for HITL resume — session continuity, not Semantic facts.</div>
                <div className="db-arch-note">{data.checkpoint_arch?.arch_note || "Maps to Architecture → MEMORY → Short-term / harness thread state."}</div>
              </button>
            )}
          </div>

          <h3 className="db-h">Paths</h3>
          <div className="db-card">
            {Object.entries(data.paths).map(([k, v]) => (
              <div key={k} className="db-meta" style={{ marginBottom: 4 }}>
                <code>{k}</code> · <span className="db-path" style={{ fontSize: 11 }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data && sub === "semantic" && (
        <div className="db-panel">
          <div className="db-meta" style={{ marginBottom: 10 }}>
            Durable structured facts in <code>user_memory</code> — preference / insight / note / entity.
          </div>
          <div className="db-arch-banner" data-arch-banner="semantic">
            {pillarById("semantic")?.arch_node && (
              <span className="db-arch-badge">Arch · {pillarById("semantic")?.arch_node}</span>
            )}
            <span>{pillarById("semantic")?.arch_note}</span>
          </div>

          {data.user_memory.length === 0 ? (
            <div className="db-empty">No facts yet — chat or write via memory tools.</div>
          ) : (
            <div className="db-scrolly">
              <table className="db-table">
                <thead>
                  <tr>
                    <th className="dbcol">type</th>
                    <th className="dbcol">content</th>
                    <th className="dbcol">user</th>
                    <th className="dbcol">when</th>
                  </tr>
                </thead>
                <tbody>
                  {data.user_memory.map((f) => (
                    <tr key={f.id}>
                      <td><code>{f.memory_type}</code></td>
                      <td className="dbcell" title={f.content}>{f.content}</td>
                      <td className="db-meta">{f.user_id}</td>
                      <td className="db-meta">{(f.created_at || "").slice(0, 19)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {data && sub === "vector" && (
        <div className="db-panel">
          <div className="db-arch-banner" data-arch-banner="vector">
            {pillarById("vector")?.arch_node && (
              <span className="db-arch-badge">Arch · {pillarById("vector")?.arch_node}</span>
            )}
            <span>{pillarById("vector")?.arch_note}</span>
          </div>
          <div className="db-meta" style={{ marginBottom: 10 }}>
            Vector store collections under <code>harness/memory/chroma_db</code>.
          </div>
          <div className="db-scrolly">
            <table className="db-table">
              <thead>
                <tr>
                  <th className="dbcol">collection</th>
                  <th className="dbcol">docs</th>
                </tr>
              </thead>
              <tbody>
                {data.chroma.map((c) => (
                  <tr key={c.name}>
                    <td><code>{c.name}</code></td>
                    <td className="db-meta">{c.count ?? "—"}</td>
                  </tr>
                ))}
                {data.chroma.length === 0 && (
                  <tr><td colSpan={2} className="db-empty">No Chroma collections found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {data && sub === "procedural" && (
        <div className="db-panel">
          <div className="db-arch-banner" data-arch-banner="procedural">
            {pillarById("procedural")?.arch_node && (
              <span className="db-arch-badge">Arch · {pillarById("procedural")?.arch_node}</span>
            )}
            <span>{pillarById("procedural")?.arch_note}</span>
          </div>
          <div className="db-card">
            <b>How to act</b>
            <p>
              Learned Q→SQL examples (<code>sql_examples</code> collection) and metric SQL templates
              (<code>metric_registry.db</code>) steer the agent without stuffing the whole DB into the prompt.
            </p>
          </div>
          <div className="db-engine-grid">
            <div className="db-engine-card" style={{ cursor: "default" }}>
              <div className="db-engine-name">SQL examples</div>
              <div className="db-engine-stat">{data.sql_examples_count ?? 0} items</div>
              <div className="db-meta">Few-shot retrieval for Text-to-SQL</div>
            </div>
            <div className="db-engine-card" style={{ cursor: "default" }}>
              <div className="db-engine-name">Metric templates</div>
              <div className="db-engine-stat">{data.metric_count} metrics</div>
              <div className="db-meta">Canonical metric SQL / aliases</div>
            </div>
          </div>
        </div>
      )}

      {data && sub === "short" && (
        <div className="db-panel">
          <div className="db-arch-banner" data-arch-banner="short">
            {pillarById("short")?.arch_node && (
              <span className="db-arch-badge">Arch · {pillarById("short")?.arch_node}</span>
            )}
            <span>{pillarById("short")?.arch_note}</span>
          </div>
          <div className="db-meta" style={{ marginBottom: 10 }}>
            Working memory for the active chat: recent turns (sliding window) + optional early-turn summary.
            Written on each completed Chat reply; also listed under sessions for Memory overview.
          </div>
          <div className="db-engine-grid" style={{ marginBottom: 12 }}>
            <div className="db-engine-card" style={{ cursor: "default" }}>
              <div className="db-engine-name">Sessions</div>
              <div className="db-engine-stat">{data.sessions.session_count}</div>
            </div>
            <div className="db-engine-card" style={{ cursor: "default" }}>
              <div className="db-engine-name">Messages</div>
              <div className="db-engine-stat">{data.sessions.message_count}</div>
            </div>
          </div>

          <h3 className="db-h">Live conversation windows</h3>
          {(data.sessions.live || []).length === 0 ? (
            <div className="db-empty" style={{ marginBottom: 12 }}>
              No live window yet — send a Chat message, then Refresh.
            </div>
          ) : (
            (data.sessions.live || []).map((w) => (
              <div key={w.id} className="db-card" style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <b>session {w.id}</b>
                  <span className="db-meta">
                    {w.recent_msgs} recent · {w.compressed_msgs} compressed
                  </span>
                </div>
                {w.summary ? (
                  <p className="db-meta" style={{ marginTop: 8 }}>
                    <b>Summary:</b> {w.summary}
                  </p>
                ) : (
                  <p className="db-meta" style={{ marginTop: 8 }}>No early-turn summary yet (window still small).</p>
                )}
                <div className="db-scrolly" style={{ marginTop: 8 }}>
                  <table className="db-table">
                    <thead>
                      <tr>
                        <th className="dbcol">role</th>
                        <th className="dbcol">content</th>
                      </tr>
                    </thead>
                    <tbody>
                      {w.messages.map((m, i) => (
                        <tr key={`${w.id}-${i}`}>
                          <td className="db-meta">{m.role}</td>
                          <td className="dbcell">{m.content}</td>
                        </tr>
                      ))}
                      {w.messages.length === 0 && (
                        <tr><td colSpan={2} className="db-empty">Empty window</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}

          <h3 className="db-h">Session message log</h3>
          {data.sessions.sessions.length === 0 ? (
            <div className="db-empty" style={{ marginBottom: 12 }}>No session log yet</div>
          ) : (
            data.sessions.sessions.map((s) => (
              <div key={s.id} className="db-card" style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <b>{s.id}</b>
                  <span className="db-meta">{s.messages} msgs</span>
                </div>
                <div className="db-scrolly" style={{ marginTop: 8 }}>
                  <table className="db-table">
                    <thead>
                      <tr>
                        <th className="dbcol">role</th>
                        <th className="dbcol">content</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(s.preview || []).map((m, i) => (
                        <tr key={`${s.id}-p-${i}`}>
                          <td className="db-meta">{m.role}</td>
                          <td className="dbcell">{m.content}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}

          <h3 className="db-h">Recent traces (episodic-ish)</h3>
          <div className="db-scrolly">
            <table className="db-table">
              <thead>
                <tr>
                  <th className="dbcol">when</th>
                  <th className="dbcol">query</th>
                  <th className="dbcol">elapsed</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_traces.map((t) => (
                  <tr key={t.trace_id}>
                    <td className="db-meta">{(t.started_at || "").replace("T", " ").slice(0, 19)}</td>
                    <td className="dbcell">{t.query}</td>
                    <td className="db-meta">{t.elapsed != null ? `${Number(t.elapsed).toFixed(1)}s` : "—"}</td>
                  </tr>
                ))}
                {data.recent_traces.length === 0 && (
                  <tr><td colSpan={3} className="db-empty">No traces yet — ask Chat something</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {data && sub === "feedback" && (
        <div className="db-panel">
          <div className="db-arch-banner" data-arch-banner="feedback">
            {data.feedback_arch && (
              <>
                <span className="db-arch-badge">Arch · {data.feedback_arch.arch_node}</span>
                <span>{data.feedback_arch.arch_note}</span>
              </>
            )}
          </div>
          <div className="db-meta" style={{ marginBottom: 10 }}>
            Thumbs up/down from Chat — feeds self-learning examples when positive.
          </div>
          {data.feedback.length === 0 ? (
            <div className="db-empty">No feedback yet</div>
          ) : (
            <div className="db-scrolly">
              <table className="db-table">
                <thead>
                  <tr>
                    <th className="dbcol">rating</th>
                    <th className="dbcol">query</th>
                    <th className="dbcol">when</th>
                  </tr>
                </thead>
                <tbody>
                  {data.feedback.map((f) => (
                    <tr key={f.id}>
                      <td><code>{f.rating}</code></td>
                      <td className="dbcell" title={f.query}>{f.query}</td>
                      <td className="db-meta">{(f.created_at || "").slice(0, 19)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {sub === "checkpoint" && (
        <div className="db-panel">
          <div className="db-arch-banner" data-arch-banner="checkpoint">
            <span className="db-arch-badge">Arch · {cpData?.arch_node || data?.checkpoint_arch?.arch_node || "Short-term (session thread)"}</span>
            <span>{cpData?.arch_note || data?.checkpoint_arch?.arch_note}</span>
          </div>
          <div className="db-card db-card-accent">
            <b>Checkpoint ≠ Semantic memory.</b>
            <p>
              These are LangGraph graph-state snapshots (thread_id) used for HITL resume and multi-turn continuity.
              Closest Architecture box: MEMORY → Short-term (session window / thread). Raw SQLite also under Database → agent_state.db.
              If REDIS_URL is set, durable checkpoints may live in Redis instead of the local file.
            </p>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="db-meta">path · <span className="db-path">{cpData?.path || "…"}</span></div>
            <button type="button" className="db-refresh" onClick={() => void loadCheckpoints()}>Refresh</button>
          </div>
          {cpError && <div className="db-error">{cpError}</div>}

          <h3 className="db-h">Live runner snapshots</h3>
          {(cpData?.live || []).length === 0 ? (
            <div className="db-empty">No live runners — send a Chat message first</div>
          ) : (
            (cpData?.live || []).map((L) => (
              <div key={L.session_id} className="db-card" style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <b>session {L.session_id}</b>
                  <span className="db-meta">{L.thread_id}</span>
                </div>
                <p className="db-meta" style={{ marginTop: 6 }}>{L.summary || "—"}</p>
                {L.next != null && L.next !== "" && <div className="db-meta">next · {String(L.next)}</div>}
                <div className="db-meta" style={{ marginTop: 6 }}>
                  keys · {(L.keys || []).length ? L.keys.join(", ") : "(empty)"}
                </div>
              </div>
            ))
          )}

          <h3 className="db-h">SQLite checkpoints ({cpData?.sqlite_count ?? 0})</h3>
          {(cpData?.checkpoints || []).length === 0 ? (
            <div className="db-empty">
              No rows in agent_state.db yet (Redis-backed deployments keep checkpoints off-disk).
            </div>
          ) : (
            <div className="db-scrolly">
              <table className="db-table">
                <thead>
                  <tr>
                    <th className="dbcol">thread</th>
                    <th className="dbcol">checkpoint_id</th>
                    <th className="dbcol">type</th>
                    <th className="dbcol">metadata</th>
                  </tr>
                </thead>
                <tbody>
                  {(cpData?.checkpoints || []).map((c) => (
                    <tr key={`${c.thread_id}-${c.checkpoint_id}`}>
                      <td className="dbcell">{c.thread_id}</td>
                      <td className="db-meta">{c.checkpoint_id}</td>
                      <td className="db-meta">{c.type || "—"}</td>
                      <td className="dbcell">{c.metadata || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

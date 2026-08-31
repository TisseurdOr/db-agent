import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./Overview.css";

/* ── 类型（与后端 server/endpoints/overview.py 返回结构对应）────────────── */

export interface LiveActivity {
  node: string;
  status: "running" | "done" | "error";
  task?: string;
  ts: number;
}

interface Stats {
  total: number;
  errored: number;
  error_rate: number;
  blocked: number;
  node_calls: Record<string, number>;
  node_errors: Record<string, number>;
  top_errors: [string, number][];
  avg_elapsed: number;
  total_tokens: number;
}

interface RuntimeStats {
  queries_total: number;
  queries_succeeded: number;
  queries_failed: number;
  tokens_total: number;
  elapsed_avg: number;
  elapsed_max: number;
  error_rate: number;
  errors_by_node: Record<string, number>;
}

interface TraceSpan {
  node: string;
  task: string;
  elapsed: number;
  total_tokens: number;
  error?: string | null;
}

interface Trace {
  trace_id: string;
  query: string;
  started_at: string;
  elapsed: number;
  blocked_by?: string | null;
  spans: TraceSpan[];
  totals: { total_tokens: number; turns: number; span_count: number };
}

interface NodeStat {
  id: string;
  label: string;
  group: string;
  calls: number;
  errors: number;
}

export interface OpikInfo {
  enabled: boolean;
  project: string;
  ui_url: string;
  url: string;
}

export interface OverviewHero {
  spent_usd: number;
  avg_turn_s: number;
  turns: number;
  agent_calls: number;
  facts: number;
  feedback: number;
  tokens: number;
  path: string;
}

export interface OverviewData {
  stats: { today: Stats; all: Stats; runtime: RuntimeStats };
  hero?: OverviewHero;
  recent: Trace[];
  nodes: NodeStat[];
  opik?: OpikInfo;
}

interface Props {
  liveActivity: LiveActivity[];
}

const REFRESH_MS = 10_000;

/* ── 小工具 ─────────────────────────────────────────────────────────────── */

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function fmtDur(sec: number): string {
  if (!sec || sec < 0) return "-";
  if (sec < 1) return `${Math.round(sec * 1000)}ms`;
  return `${sec.toFixed(1)}s`;
}

function fmtNum(n: number): string {
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(2)}k`;
  return String(n);
}

/* ── 统计卡片 ───────────────────────────────────────────────────────────── */

export function StatCard({ label, value, sub, tone }: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "bad" | "warn";
}) {
  const color = tone === "good" ? "#4ade80" : tone === "bad" ? "#f87171" : tone === "warn" ? "#fbbf24" : "#e2e8f0";
  return (
    <div className="ov-card">
      <div className="ov-card-label">{label}</div>
      <div className="ov-card-value" style={{ color }}>{value}</div>
      {sub && <div className="ov-card-sub">{sub}</div>}
    </div>
  );
}

/** Waku-style Overview strip: live · updated · path + 6 metric tiles */
export function OverviewHeroStrip({
  hero,
  live,
  updatedAt,
}: {
  hero: OverviewHero;
  live: boolean;
  updatedAt: Date | null;
}) {
  const ago = (() => {
    if (!updatedAt) return "—";
    const s = Math.max(0, Math.round((Date.now() - updatedAt.getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  })();

  const tiles: { value: string; label: string; tone?: "good" | "muted" }[] = [
    { value: `$${hero.spent_usd.toFixed(2)}`, label: "spent · all-time", tone: "good" },
    { value: `${hero.avg_turn_s.toFixed(1)}s`, label: "avg turn" },
    { value: String(hero.turns), label: "turns" },
    { value: String(hero.agent_calls), label: "agent calls" },
    { value: String(hero.facts), label: "facts" },
    { value: String(hero.feedback), label: "feedback" },
  ];

  return (
    <div className="ov-hero">
      <div className="ov-hero-head">
        <h2 className="ov-hero-title">Overview</h2>
        <div className="ov-hero-meta">
          <span className={`ov-hero-live ${live ? "on" : ""}`}>
            <span className="ov-hero-dot" /> {live ? "live" : "idle"}
          </span>
          <span className="ov-hero-sep">·</span>
          <span>updated {ago}</span>
          <span className="ov-hero-sep">·</span>
          <span className="ov-hero-path" title={hero.path}>{hero.path}</span>
        </div>
      </div>
      <div className="ov-hero-tiles">
        {tiles.map((t) => (
          <div key={t.label} className="ov-hero-tile">
            <div className={`ov-hero-value ${t.tone || ""}`}>{t.value}</div>
            <div className="ov-hero-label">{t.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * 架构图 SVG —— 仿 waku-agent dashboard 的实时架构图
 *
 * 每个盒子带 data-node="id"，每条连线带 data-edge="id"；
 * SSE 事件（step_start/step_end/error）→ STAGE 映射 → 点亮对应节点/连线。
 * 布局对应 db-agent HARNESS.md 的全景：
 *   用户 → 输入护栏 → Router → Agent 池 → 质量闭环 → Tools → 数据源 → 回复
 *   右侧：LLM OPS（Trace/Opik/monitor/Eval）· MEMORY（短/长/向量）
 *   底部：CONSTRAINTS 横切（Entitlement · Guardrails · HITL · Retry/熔断/幂等）
 * ══════════════════════════════════════════════════════════════════════════ */

/** 节点可点开：标题 / 职责 / 关键实现 / 典型行为 */
type NodeDetail = {
  title: string;
  group: string;
  summary: string;
  bullets: string[];
  files?: string[];
};

const NODE_DETAILS: Record<string, NodeDetail> = {
  user: {
    title: "User",
    group: "Gateway",
    summary: "Entry points: CLI, Web Chat, Streamlit — all share MultiAgentRunner.",
    bullets: ["Natural-language questions", "Optional RBAC identity + DQ toggle", "HITL approvals resume here"],
    files: ["main.py", "server/endpoints/query.py", "frontend/"],
  },
  preprocessing: {
    title: "Guardrails",
    group: "Constraints",
    summary: "Deterministic pre-graph checks: injection, write intent, empty/overlong input.",
    bullets: ["L1 input injection", "L2 SQL write block (SELECT only)", "L3 output PII / leak detection"],
    files: ["harness/constraints/guardrails.py"],
  },
  router: {
    title: "Router",
    group: "Orchestration",
    summary: "Intent dispatch: hard rules → context inherit / LRU → LLM classify.",
    bullets: ["Hard rules for chitchat / meta / engine keywords", "RouterCache LRU skips repeat LLM", "JSON plan → downstream agents"],
    files: ["harness/orchestration/multi/router.py", "harness/orchestration/multi/cache.py"],
  },
  clarify: {
    title: "Clarify",
    group: "Orchestration",
    summary: "Low confidence → ask 2–3 clarifying questions. Next: user answers → resume Router.",
    bullets: ["Fuzzy entities / time ranges", "interrupt until user replies", "Next step: back to Router for a new plan"],
    files: ["harness/orchestration/multi/nodes.py"],
  },
  graph: {
    title: "Agent Graph",
    group: "Orchestration",
    summary: "LangGraph orchestration of 6 specialist agents + quality gates; Checkpointer for HITL resume.",
    bullets: ["StateGraph topology", "Task board persisted ✓/✗", "Replan on failure ≤1"],
    files: ["harness/orchestration/multi/graph.py", "harness/orchestration/multi/runner.py"],
  },
  sql: {
    title: "SQL Agent",
    group: "Agent Pool",
    summary: "SQLite business DB only: Schema Linking → SELECT → rowsets (no business narrative).",
    bullets: ["discover_relevant_schema / describe_table", "run_query is SELECT-only", "few-shot / templates injectable"],
    files: ["harness/orchestration/multi/agents.py", "harness/tools/query.py"],
  },
  hbase: {
    title: "HBase Agent",
    group: "Agent Pool",
    summary: "NoSQL KV simulator: Shell-style commands on in-memory tables.",
    bullets: ["get / scan / put / delete …", "Destructive writes go through HITL", "~12 operations"],
    files: ["harness/tools/hbase.py"],
  },
  hive: {
    title: "Hive Agent",
    group: "Agent Pool",
    summary: "Warehouse-style layers (ods/dwd/dim) with HiveQL dialect hints.",
    bullets: ["Hive-style tables", "search_hive_syntax", "Partition-column aware"],
    files: ["harness/tools/hive.py"],
  },
  strategy: {
    title: "Strategy Agent",
    group: "Agent Pool",
    summary: "Policy docs + metric definitions — no raw detail rows, actionable explanations only.",
    bullets: ["search_knowledge_base", "lookup_metric dictionary", "Definition consistency"],
    files: ["harness/tools/knowledge.py", "harness/tools/metrics.py"],
  },
  analysis: {
    title: "Analysis Agent",
    group: "Agent Pool",
    summary: "Synthesize upstream results into trends / comparisons / charts — does not write SQL.",
    bullets: ["analyze_results", "compare_periods", "render_chart"],
    files: ["harness/tools/analysis.py", "harness/tools/chart.py"],
  },
  data_quality: {
    title: "Data Quality",
    group: "Agent Pool",
    summary: "Optional pre-check: NULLs / outliers / date scan; controlled by the DQ toggle.",
    bullets: ["Injected on first run or after timeout", "Web default OFF", "Issues written into context"],
    files: ["harness/orchestration/multi/runner.py"],
  },
  tools: {
    title: "Tools",
    group: "Execution",
    summary: "@tool atomic capabilities: schema / query / analysis / chart / knowledge / big-data.",
    bullets: ["JSON Schema auto-generated", "TOOL_HANDLERS dispatch", "Add a tool without rewriting the Loop"],
    files: ["harness/tools/"],
  },
  confidence_gate: {
    title: "Confidence Gate",
    group: "Quality Loop",
    summary: "After SQL, before Analysis: 6-dimension LLM score. Below ~0.7 → HITL approve/reject (serial with Reflection).",
    bullets: ["Right after SQL Agent", "Low score → interrupt approval", "Pass/approve → continue toward Analysis"],
    files: ["harness/orchestration/multi/nodes.py", "harness/constraints/confidence.py"],
  },
  reflection: {
    title: "Reflection",
    group: "Quality Loop",
    summary: "Runs after Analysis final_answer; failures loop back to Analysis (≤2). Serial with Confidence, not parallel.",
    bullets: ["After Analysis", "Completeness / faithfulness / usefulness", "Fail → Analysis rewrite ≤2"],
    files: ["harness/orchestration/multi/nodes.py"],
  },
  reply: {
    title: "Reply",
    group: "Gateway",
    summary: "Final answer through output guardrails back to the user (SSE / CLI / UI).",
    bullets: ["guard_output", "Optional chart panel", "Thumbs-up feeds example store"],
    files: ["server/runner_wrapper.py", "server/sse.py"],
  },
  short_mem: {
    title: "Short-term Memory",
    group: "Memory",
    summary: "Same-session sliding window + LLM summary past the window.",
    bullets: ["ConversationManager max_recent≈10", "HybridWindow compression (single mode)", "Injected into Analysis context"],
    files: ["harness/memory/short_term_memory.py"],
  },
  long_mem: {
    title: "Long-term Memory",
    group: "Memory",
    summary: "Cross-session persistence: successful SQL / HITL / thumbs-up through quality gates.",
    bullets: ["user_memory SQLite", "Self-learning few-shot store", "AUTO_LEARN_SQL can disable"],
    files: ["harness/context/sql_examples.py", "harness/context/feedback.py"],
  },
  vector_store: {
    title: "Vector Store",
    group: "Memory",
    summary: "Semantic recall: chat history / Schema Linking / few-shot retrieval.",
    bullets: ["Chroma (swappable Milvus)", "memory_controller anti-pollution", "Skip chitchat / time-order for meta Qs"],
    files: ["harness/memory/vector_store.py", "harness/memory/memory_controller.py"],
  },
  trace: {
    title: "Trace JSONL",
    group: "LLM Ops",
    summary: "Per-turn local log: node latency, tokens, errors — replayable.",
    bullets: ["logs/traces/*.jsonl", "Zero-dep always-on", "OpenTelemetry-inspired"],
    files: ["harness/observation/tracer.py"],
  },
  opik: {
    title: "Opik",
    group: "LLM Ops",
    summary: "Optional distributed tracing: LangGraph tree, LLM spans, Feedback Score. Click opens Opik UI.",
    bullets: ["OPIK_ENABLED switch", "Dataset / Experiment", "Complements local Trace"],
    files: ["harness/observation/opik_tracing.py"],
  },
  eval: {
    title: "Eval · Judge",
    group: "LLM Ops",
    summary: "Structured regression: guard / routing / quality / edge + independent LLM Judge.",
    bullets: ["eval_runner fast/full", "LLM-as-Judge (Kimi)", "CI anti-regression"],
    files: ["tests/eval_runner.py", "tests/eval_cases.py"],
  },
  monitor: {
    title: "pipeline_monitor",
    group: "LLM Ops",
    summary: "Demo pipeline health board: job SLA and failure alerts.",
    bullets: ["Standalone module", "Prometheus /api/metrics", "Ops perspective"],
    files: ["harness/observation/pipeline_monitor/"],
  },
  constraints: {
    title: "Constraints",
    group: "Constraints",
    summary: "Cross-cutting hard limits: ACL, guardrails, HITL, retry/circuit — fail safely.",
    bullets: ["5-role RBAC + row rewrite", "Sensitive columns / HBase write HITL", "API backoff · SQL self-heal · replan"],
    files: ["harness/constraints/entitlement.py", "harness/constraints/guardrails.py"],
  },
};

/** SSE step node → 架构图节点 + 连线（对齐 waku STAGE 思想） */
const STAGE: Record<string, { nodes: string[]; edges: string[]; label: string }> = {
  init:            { nodes: ["user"],          edges: ["e-user-guard"],     label: "message in" },
  connecting:      { nodes: ["user"],          edges: ["e-user-guard"],     label: "connecting" },
  preprocessing:   { nodes: ["preprocessing"], edges: ["e-guard-router"],   label: "guardrail check" },
  router:          { nodes: ["router"],        edges: ["e-router-agents"],  label: "routing" },
  clarify:         { nodes: ["clarify"],       edges: ["e-clarify-router"], label: "clarifying" },
  sql:             { nodes: ["sql", "tools"],  edges: ["e-agents-tools"],   label: "SQL agent" },
  hbase:           { nodes: ["hbase", "tools"],edges: ["e-agents-tools"],   label: "HBase agent" },
  hive:            { nodes: ["hive", "tools"], edges: ["e-agents-tools"],   label: "Hive agent" },
  strategy:        { nodes: ["strategy", "tools"], edges: ["e-agents-tools"], label: "policy search" },
  analysis:        { nodes: ["analysis", "tools"], edges: ["e-agents-tools"], label: "analysis" },
  data_quality:    { nodes: ["data_quality"],  edges: ["e-router-agents"],  label: "data quality" },
  confidence_gate: { nodes: ["confidence_gate"], edges: ["e-agents-quality", "e-conf-hitl"], label: "confidence / HITL" },
  reflection:      { nodes: ["reflection"],    edges: ["e-conf-reflect", "e-quality-reply"], label: "reflection" },
  graph:           { nodes: ["graph"],         edges: ["e-agents-quality"], label: "orchestration" },
  guardrail:       { nodes: ["preprocessing"], edges: [],                   label: "blocked" },
};

const DEFAULT_OPIK_URL = "http://localhost:5173";

/** waku 式布局：主链路横排、少箭头、右侧 OPS、下方 MEMORY —— 刻意留白 */
export function ArchSVG({
  nodes,
  liveActivity,
  opik,
  theme = "dark",
}: {
  nodes: NodeStat[];
  liveActivity: LiveActivity[];
  opik?: OpikInfo | null;
  theme?: "dark" | "light";
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const detail = selected ? NODE_DETAILS[selected] : null;
  const selectedStat = selected ? (nodes.find((n) => n.id === selected) ?? null) : null;
  const opikUrl = opik?.url || opik?.ui_url || DEFAULT_OPIK_URL;

  const stat = (id: string): NodeStat =>
    nodes.find((n) => n.id === id) ?? { id, label: id, group: "", calls: 0, errors: 0 };

  const openNode = useCallback((id: string) => {
    if (id === "opik") {
      window.open(opikUrl, "_blank", "noopener,noreferrer");
    }
    setSelected((prev) => (prev === id ? null : id));
  }, [opikUrl]);

  const statusById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const act of liveActivity) {
      const stage = STAGE[act.node];
      for (const id of stage?.nodes ?? [act.node]) map[id] = act.status;
    }
    return map;
  }, [liveActivity]);

  const activeEdges = useMemo(() => {
    const set = new Set<string>();
    for (const act of liveActivity) {
      const stage = STAGE[act.node];
      if (!stage || act.status !== "running") continue;
      for (const e of stage.edges) set.add(e);
    }
    return set;
  }, [liveActivity]);

  const latest = liveActivity.length > 0 ? liveActivity[liveActivity.length - 1] : null;
  const statusLabel = latest ? (STAGE[latest.node]?.label ?? latest.node) : null;
  const statusTone = latest?.status ?? "";

  const nodeCls = (id: string): string => {
    const s = statusById[id];
    return ["arch-node", s ? `arch-${s}` : "", stat(id).calls === 0 && !s ? "arch-idle" : ""]
      .filter(Boolean)
      .join(" ");
  };

  const edgeCls = (id: string) =>
    ["arch-edge", activeEdges.has(id) ? "arch-edge-live" : ""].filter(Boolean).join(" ");

  const Badge = ({ id, x, y, w }: { id: string; x: number; y: number; w: number }) => {
    const n = stat(id);
    if (n.calls <= 0) return null;
    return (
      <g className="arch-count">
        <rect x={x + w - 34} y={y - 8} width="34" height="16" rx="8" />
        <text x={x + w - 17} y={y + 4}>{n.errors > 0 ? `✗${n.errors}` : String(n.calls)}</text>
      </g>
    );
  };

  const Box = ({
    id, x, y, w, h, title, sub,
  }: { id: string; x: number; y: number; w: number; h: number; title: string; sub?: string }) => {
    const pad = 12;
    const titleY = sub ? y + pad + 9 : y + h / 2 + 4;
    const subY = y + h - pad;
    return (
    <g
      data-node={id}
      className={`${nodeCls(id)}${selected === id ? " arch-selected" : ""}${NODE_DETAILS[id] ? " arch-clickable" : ""}`}
      onClick={(e) => {
        e.stopPropagation();
        if (NODE_DETAILS[id]) openNode(id);
      }}
      role={NODE_DETAILS[id] ? "button" : undefined}
      tabIndex={NODE_DETAILS[id] ? 0 : undefined}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && NODE_DETAILS[id]) {
          e.preventDefault();
          openNode(id);
        }
      }}
    >
      <title>{id === "opik" ? "Open Opik UI" : NODE_DETAILS[id] ? `Details: ${NODE_DETAILS[id].title}` : title}</title>
      <rect className="arch-bx" x={x} y={y} width={w} height={h} rx="9" />
      <text className="arch-nt" x={x + 12} y={titleY}>{title}</text>
      {sub && <text className="arch-ns" x={x + 12} y={subY}>{sub}</text>}
      <Badge id={id} x={x} y={y} w={w} />
    </g>
    );
  };

  return (
    <div className="ov-arch" data-theme={theme}>
      <div className="ov-arch-head">
        <span className="ov-arch-title">HARNESS · LIVE ARCHITECTURE</span>
        <div className="ov-arch-head-right">
          {statusLabel && (
            <span className={`arch-status arch-status-${statusTone}`}>
              <span className="live-dot" /> {statusLabel}
            </span>
          )}
        </div>
      </div>
      <div className="ov-arch-scroll">
        {/*
          外框 inset：内容与虚线边框保持 ≥16px。
          reply 弧线走外框内侧，不贴边；已去掉底部 CONSTRAINTS 条。
        */}
        <svg viewBox="0 0 1080 640" className="arch" role="img" aria-label="db-agent architecture">
          <defs>
            <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" className="arch-arr" />
            </marker>
          </defs>

          {/* HARNESS 外框 —— 标签在框外；框内顶边与内容/弧线保持 ≥28px */}
          <text className="arch-group-label" x={44} y={16}>HARNESS — runs locally · the turn inside is ephemeral</text>
          <rect className="arch-container" x={28} y={32} width={1024} height={588} rx="16" />

          {/* ── 主链路（整体下移，给 reply 弧线留空）── */}
          <Box id="user" x={52} y={96} w={118} h={52} title="User" sub="CLI · Web" />
          <path className={edgeCls("e-user-guard")} data-edge="e-user-guard" d="M170 122 L194 122" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={172} y={114}>query</text>

          <Box id="preprocessing" x={196} y={96} w={136} h={52} title="Guardrails" sub="L1 · L2 · L3" />
          <path className={edgeCls("e-guard-router")} data-edge="e-guard-router" d="M332 122 L356 122" markerEnd="url(#arr)" />

          <Box id="router" x={358} y={96} w={120} h={52} title="Router" sub="Rules → LRU → LLM" />
          <Box id="clarify" x={368} y={168} w={118} h={52} title="Clarify" sub="ask user → resume" />
          <path className={edgeCls("e-router-clarify")} data-edge="e-router-clarify" d="M418 148 L418 168" markerEnd="url(#arr)" />
          {/* Clarify 下一步：用户答完 → 回到 Router 重规划 */}
          <path
            className={`${edgeCls("e-clarify-router")} arch-edge-dash`}
            data-edge="e-clarify-router"
            d="M368 194 C330 194 318 160 318 122 L356 122"
            markerEnd="url(#arr)"
          />
          <text className="arch-edge-label" x={288} y={168}>resume</text>

          <g
            data-node="graph"
            className={`${nodeCls("graph")} arch-clickable${selected === "graph" ? " arch-selected" : ""}`}
            onClick={(e) => { e.stopPropagation(); openNode("graph"); }}
            role="button"
            tabIndex={0}
          >
            <title>Details: Agent Graph</title>
            <rect className="arch-loopbox" x={506} y={80} width={268} height={232} rx="12" />
          </g>
          <text className="arch-group-label" x={520} y={74}>AGENT GRAPH · 6 agents</text>
          <path className={edgeCls("e-router-agents")} data-edge="e-router-agents" d="M478 122 L504 122" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={482} y={114}>task</text>

          <Box id="sql" x={520} y={94} w={114} h={40} title="SQL" />
          <Box id="hbase" x={644} y={94} w={114} h={40} title="HBase" />
          <Box id="hive" x={520} y={142} w={114} h={40} title="Hive" />
          <Box id="strategy" x={644} y={142} w={114} h={40} title="Strategy" />
          <Box id="analysis" x={520} y={190} w={114} h={40} title="Analysis" />
          <Box id="data_quality" x={644} y={190} w={114} h={40} title="Data Quality" />

          <Box id="tools" x={520} y={242} w={238} h={52} title="Tools" sub="schema · query · chart · knowledge" />
          <path className={edgeCls("e-agents-tools")} data-edge="e-agents-tools" d="M592 230 L592 242" markerEnd="url(#arr)" />
          <path className="arch-edge arch-edge-dash" d="M632 242 L632 230" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={640} y={238}>act</text>

          {/* 串行：SQL → Confidence(+HITL) → … Analysis → Reflection → Reply，不同时 */}
          <Box id="confidence_gate" x={520} y={320} w={238} h={52} title="Confidence" sub={"after SQL · HITL if <0.7"} />
          <path className={edgeCls("e-agents-quality")} data-edge="e-agents-quality" d="M640 294 L640 320" markerEnd="url(#arr)" />
          {/* HITL：低分 interrupt → User 审批 */}
          <path
            className={`${edgeCls("e-conf-hitl")} arch-edge-dash`}
            data-edge="e-conf-hitl"
            d="M520 346 C420 346 200 280 170 148"
            markerEnd="url(#arr)"
          />
          <text className="arch-edge-label" x={250} y={300}>HITL approve?</text>

          <path className={edgeCls("e-conf-reflect")} data-edge="e-conf-reflect" d="M640 372 L640 388" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={648} y={384}>after Analysis</text>
          <Box id="reflection" x={520} y={388} w={238} h={52} title="Reflection" sub="≤2 → Analysis · then Reply" />
          {/* Reflection 退回 Analysis（虚线回环） */}
          <path
            className="arch-edge arch-edge-dash"
            data-edge="e-reflect-analysis"
            d="M520 414 C470 414 460 250 520 250"
            markerEnd="url(#arr)"
          />
          <text className="arch-edge-label" x={430} y={340}>retry</text>

          <path className={edgeCls("e-quality-reply")} data-edge="e-quality-reply" d="M758 414 L824 140" markerEnd="url(#arr)" />
          <Box id="reply" x={824} y={100} w={118} h={52} title="Reply" sub="→ back to user" />

          {/* LLM OPS */}
          <rect className="arch-container arch-ops" x={824} y={180} width={188} height={300} rx="14" />
          <text className="arch-group-label" x={836} y={200}>LLM OPS</text>
          <text className="arch-ns" x={836} y={216}>observe · eval · improve</text>
          <path className={edgeCls("e-reply-ops")} data-edge="e-reply-ops" d="M883 152 L883 180" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={892} y={170}>each turn</text>

          <Box id="trace" x={836} y={228} w={164} h={52} title="Trace JSONL" sub="one per turn" />
          <path className="arch-edge" d="M918 280 L918 292" markerEnd="url(#arr)" />
          <Box id="opik" x={836} y={292} w={164} h={52} title="Opik" sub="distributed spans" />
          <path className="arch-edge" d="M918 344 L918 356" markerEnd="url(#arr)" />
          <Box id="eval" x={836} y={356} w={164} h={52} title="Eval · Judge" sub="regression gate" />
          <path className="arch-edge" d="M918 408 L918 420" markerEnd="url(#arr)" />
          <Box id="monitor" x={836} y={420} w={164} h={40} title="pipeline_monitor" />

          {/* MEMORY —— 卡片尺寸贴近主链路模块（~118×52） */}
          <text className="arch-group-label" x={52} y={478}>MEMORY — short / long + vector</text>
          <rect className="arch-memgroup" x={48} y={488} width={420} height={88} rx="12" />
          <Box id="short_mem" x={60} y={504} w={120} h={52} title="Short-term" sub="session window" />
          <Box id="long_mem" x={196} y={504} w={120} h={52} title="Long-term" sub="self-learning" />
          <Box id="vector_store" x={332} y={504} w={120} h={52} title="Vector store" sub="Chroma · HyDE" />

          {/* OPS → harness 反馈 */}
          <path className="arch-edge arch-edge-dash" d="M824 440 C760 460 680 490 468 520" markerEnd="url(#arr)" />
          <text className="arch-edge-label" x={620} y={480}>improved config</text>

          {/* ── 引导线后绘，避免被卡片盖住 ── */}
          {/* reply → user：顶边 y=32，弧线 y=64，间距 32 */}
          <path
            className="arch-edge"
            data-edge="e-reply-user"
            d="M883 100 C883 72 860 64 800 64 L160 64 C120 64 112 78 112 96"
            markerEnd="url(#arr)"
          />
          <text className="arch-edge-label" x={460} y={56} textAnchor="middle">reply, out the same gateway</text>

          {/* reply → memory：右侧外廊绕行，再从上方进入 Memory（不穿卡片） */}
          <path
            className="arch-edge arch-edge-dash"
            data-edge="e-reply-save"
            d="M942 126 L1032 126 L1032 560 L468 560 L468 540"
            markerEnd="url(#arr)"
          />
          {/* label sits next to MEMORY so it stays readable */}
          <text className="arch-edge-label" x={480} y={552}>save / learn → memory</text>
        </svg>
      </div>

      {detail && (
        <div className="arch-detail" role="dialog" aria-label={detail.title}>
          <div className="arch-detail-head">
            <div>
              <div className="arch-detail-group">{detail.group}</div>
              <div className="arch-detail-title">{detail.title}</div>
            </div>
            <button type="button" className="arch-detail-close" onClick={() => setSelected(null)} aria-label="Close">
              ✕
            </button>
          </div>
          <p className="arch-detail-summary">{detail.summary}</p>
          <ul className="arch-detail-bullets">
            {detail.bullets.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          {detail.files && detail.files.length > 0 && (
            <div className="arch-detail-files">
              <div className="arch-detail-files-label">Code</div>
              {detail.files.map((f) => (
                <code key={f}>{f}</code>
              ))}
            </div>
          )}
          {selected === "opik" && (
            <a
              className="arch-detail-link"
              href={opikUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open Opik{opik?.project ? ` · ${opik.project}` : ""} ↗
            </a>
          )}
          {selectedStat && selectedStat.calls > 0 && (
            <div className="arch-detail-stats">
              Calls <strong>{selectedStat.calls}</strong>
              {selectedStat.errors > 0 && (
                <> · errors <strong style={{ color: "#f87171" }}>{selectedStat.errors}</strong></>
              )}
            </div>
          )}
        </div>
      )}

      <div className="ov-arch-legend">
        <span><span className="legend-dot legend-running" /> Running</span>
        <span><span className="legend-dot legend-done" /> Done</span>
        <span><span className="legend-dot legend-err" /> Error</span>
        <span><span className="legend-line" /> flowing edge</span>
        <span className="ov-arch-hint">Click a node for details · Opik opens the observability UI</span>
      </div>
    </div>
  );
}

/* ── 节点统计条 ─────────────────────────────────────────────────────────── */

export function NodeBars({ nodes }: { nodes: NodeStat[] }) {
  const max = Math.max(1, ...nodes.map((n) => n.calls));
  const [errNode, setErrNode] = useState<NodeStat | null>(null);
  const [errItems, setErrItems] = useState<{
    trace_id?: string;
    opik_trace_id?: string | null;
    thread_id?: string | null;
    query: string;
    error: string;
    task?: string;
    started_at?: string;
    elapsed?: number;
    turns?: number;
    max_turns?: number;
    tokens?: number;
    path?: string;
    reason?: string;
  }[]>([]);
  const [errLoading, setErrLoading] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const openErrors = async (n: NodeStat) => {
    if (n.errors <= 0) return;
    setErrNode(n);
    setErrLoading(true);
    setErrMsg(null);
    try {
      const res = await fetch(`/api/overview/node-errors/${encodeURIComponent(n.id)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setErrItems(json.items || []);
    } catch (e) {
      setErrItems([]);
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setErrLoading(false);
    }
  };

  return (
    <div className="ov-panel">
      <div className="ov-panel-title">Node call distribution (history)</div>
      <div className="ov-bars">
        {nodes.filter((n) => n.calls > 0).map((n) => (
          <div key={n.id} className="ov-bar-row">
            <span className="ov-bar-label">{n.label}</span>
            <div className="ov-bar-track">
              <div className="ov-bar-fill" style={{ width: `${(n.calls / max) * 100}%` }} />
              {n.errors > 0 && (
                <div className="ov-bar-err" style={{ width: `${(n.errors / max) * 100}%` }} />
              )}
            </div>
            <span className="ov-bar-val">
              {n.errors > 0 && (
                <button
                  type="button"
                  className="ov-err-btn"
                  title={`View ${n.errors} error(s) for ${n.label}`}
                  onClick={() => void openErrors(n)}
                >
                  {n.errors}✗
                </button>
              )}
              {" "}{n.calls}
            </span>
          </div>
        ))}
        {nodes.every((n) => n.calls === 0) && <div className="ov-empty">No traces yet — ask something in Chat</div>}
      </div>

      {errNode && (
        <div className="ov-err-drawer" role="dialog" aria-label={`${errNode.label} errors`}>
          <div className="ov-err-drawer-head">
            <div>
              <div className="ov-err-drawer-title">{errNode.label} · errors</div>
              <div className="ov-err-drawer-sub">From Trace JSONL · click ✗ on a node to inspect</div>
            </div>
            <button type="button" className="ov-err-close" onClick={() => setErrNode(null)}>✕</button>
          </div>
          {errLoading && <div className="ov-empty">Loading…</div>}
          {errMsg && <div className="ov-error">{errMsg}</div>}
          {!errLoading && !errMsg && errItems.length === 0 && (
            <div className="ov-empty">No error spans found in traces</div>
          )}
          <div className="ov-err-hint">
            <b>What “超过最大轮数” means:</b> the agent used up its tool-call budget
            (default <code>max_turns=8</code>) without finishing — often because the entity
            is missing, the task is ambiguous, or it kept re-querying. A later replan on the
            same trace may still succeed.
          </div>
          <div className="ov-err-list">
            {errItems.map((it, i) => (
              <div key={`${it.trace_id}-${it.task}-${i}`} className="ov-err-item">
                <div className="ov-err-when">{(it.started_at || "").replace("T", " ").slice(0, 19)}</div>
                <div className="ov-err-ids">
                  <span><b>trace</b> <code>{it.trace_id || "—"}</code></span>
                  <span><b>thread</b> <code>{it.thread_id || "(not recorded — new traces will include it)"}</code></span>
                  {it.opik_trace_id && (
                    <span><b>opik</b> <code>{it.opik_trace_id}</code></span>
                  )}
                </div>
                <div className="ov-err-query" title={it.query}>
                  <b>user query</b> · {it.query || "(no query)"}
                </div>
                {it.task && (
                  <div className="ov-err-task"><b>agent task</b> · {it.task}</div>
                )}
                <div className="ov-err-msg">{it.error}</div>
                <div className="ov-err-meta">
                  turns {it.turns ?? "?"} / {it.max_turns ?? 8}
                  {it.elapsed != null && <> · {Number(it.elapsed).toFixed(1)}s</>}
                  {it.tokens != null && <> · {it.tokens}t</>}
                </div>
                {it.reason && <div className="ov-err-reason">{it.reason}</div>}
                {it.path && <div className="ov-err-path"><b>span path</b> · {it.path}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 最近查询列表 ───────────────────────────────────────────────────────── */

const NODE_SHORT: Record<string, string> = {
  preprocessing: "guard", router: "router", clarify: "clarify", data_quality: "DQ",
  sql: "SQL", hbase: "HBase", hive: "Hive", strategy: "policy", analysis: "analysis",
  confidence_gate: "gate", reflection: "reflect", graph: "graph", guardrail: "blocked",
};

export function RecentList({ recent }: { recent: Trace[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div className="ov-panel">
      <div className="ov-panel-title">Recent queries ({recent.length})</div>
      <div className="ov-recent">
        {recent.length === 0 && <div className="ov-empty">No records yet</div>}
        {recent.map((t) => {
          const path = t.spans.map((s) => NODE_SHORT[s.node] ?? s.node).join(" → ");
          const errSpans = t.spans.filter((s) => s.error);
          const hasErr = errSpans.length > 0;
          const open = openId === t.trace_id;
          return (
            <div key={t.trace_id} className={`ov-recent-block ${hasErr ? "has-err" : ""}`}>
              <button
                type="button"
                className="ov-recent-row"
                title={hasErr ? "Click to show errors" : t.query}
                onClick={() => setOpenId(open ? null : (hasErr ? t.trace_id : null))}
                disabled={!hasErr}
              >
                <span className="ov-recent-time">{fmtTime(t.started_at)}</span>
                <span className={`ov-recent-query ${hasErr ? "ov-recent-err" : ""}`}>{t.query}</span>
                <span className="ov-recent-path">{path || "-"}</span>
                {t.blocked_by && <span className="ov-recent-blocked">🚫 {t.blocked_by}</span>}
                <span className="ov-recent-meta">
                  {hasErr && <span className="ov-err-btn">{errSpans.length}✗ </span>}
                  {fmtDur(t.elapsed)} · {t.totals.total_tokens}t
                </span>
              </button>
              {open && (
                <div className="ov-err-list nested">
                  {errSpans.map((s, i) => (
                    <div key={i} className="ov-err-item">
                      <div className="ov-err-task">{NODE_SHORT[s.node] ?? s.node} · {s.task}</div>
                      <div className="ov-err-msg">{s.error}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


/* ── 流程图 Tab：统计卡片 + 实时架构图 ─────────────────────────────────── */

export function ArchView({
  data,
  liveActivity,
  theme = "dark",
  updatedAt = null,
}: {
  data: OverviewData;
  liveActivity: LiveActivity[];
  theme?: "dark" | "light";
  updatedAt?: Date | null;
}) {
  const live = liveActivity.some((a) => a.status === "running");
  const hero = data.hero ?? {
    spent_usd: 0,
    avg_turn_s: data.stats.all.avg_elapsed,
    turns: data.stats.all.total,
    agent_calls: Object.values(data.stats.all.node_calls || {}).reduce((a, b) => a + b, 0),
    facts: 0,
    feedback: 0,
    tokens: data.stats.all.total_tokens,
    path: "",
  };
  return (
    <div className="ov-arch-page">
      <OverviewHeroStrip hero={hero} live={live} updatedAt={updatedAt} />
      <ArchSVG nodes={data.nodes} liveActivity={liveActivity} opik={data.opik} theme={theme} />
    </div>
  );
}

/* ── 主组件 ─────────────────────────────────────────────────────────────── */

export default function Overview({ liveActivity }: Props) {
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

  const s = data?.stats;
  const successRate = s ? (1 - s.today.error_rate) * 100 : 0;
  const liveRunning = liveActivity.some((a) => a.status === "running");

  return (
    <div className="ov-wrap">
      <div className="ov-header">
        <div>
          <h2 className="ov-title">Overview · ops cockpit</h2>
          <div className="ov-sub">
            {liveRunning
              ? <span className="ov-live">● Query running — nodes light up live</span>
              : "Live monitor · auto-refresh every 10s"}
            {lastUpdated && (
              <span className="ov-updated">Updated {lastUpdated.toLocaleTimeString("en-US", { hour12: false })}</span>
            )}
          </div>
        </div>
        <button className="ov-refresh" onClick={() => void load()} disabled={!!error && !data}>Refresh</button>
      </div>

      {error && !data && <div className="ov-error">Failed to load: {error}</div>}

      {data && (
        <>
          <div className="ov-cards">
            <StatCard label="Queries today" value={String(s?.today.total ?? 0)} sub={`history ${s?.all.total ?? 0}`} />
            <StatCard label="Success rate" value={`${successRate.toFixed(1)}%`} tone={successRate >= 95 ? "good" : successRate >= 80 ? "warn" : "bad"} sub={`failed ${s?.today.errored ?? 0}`} />
            <StatCard label="Avg latency" value={fmtDur(s?.today.avg_elapsed ?? 0)} sub={`max ${fmtDur(s?.runtime.elapsed_max ?? 0)}`} />
            <StatCard label="Tokens" value={fmtNum(s?.today.total_tokens ?? 0)} sub={`today · history ${fmtNum(s?.all.total_tokens ?? 0)}`} />
            <StatCard label="Guardrail block" value={String(s?.today.blocked ?? 0)} sub="today" tone={(s?.today.blocked ?? 0) > 0 ? "warn" : "good"} />
            <StatCard label="Runtime queries" value={String(s?.runtime.queries_total ?? 0)} sub={`error ${((s?.runtime.error_rate ?? 0) * 100).toFixed(1)}%`} tone={(s?.runtime.error_rate ?? 0) > 0.2 ? "bad" : undefined} />
          </div>

          <ArchSVG nodes={data.nodes} liveActivity={liveActivity} opik={data.opik} />

          <div className="ov-bottom">
            <NodeBars nodes={data.nodes} />
            <RecentList recent={data.recent} />
          </div>
        </>
      )}
    </div>
  );
}

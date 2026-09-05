import { useRef, useCallback, useState, useEffect } from "react";
import { useChat } from "./hooks/useChat";
import { useSSE } from "./hooks/useSSE";
import Sidebar, { type MainTabId } from "./components/Sidebar";
import MainTabs from "./components/MainTabs";
import ChatDock from "./components/ChatDock";
import HITLDialog from "./components/HITLDialog";
import type { LiveActivity } from "./components/Overview";
import "./App.css";

const UI_THEME_KEY = "db-agent-ui-theme";
type UiTheme = "dark" | "light";

function readUiTheme(): UiTheme {
  try {
    const saved = localStorage.getItem(UI_THEME_KEY)
      || localStorage.getItem("db-agent-arch-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* ignore */ }
  return "dark";
}

/** 已完成活动保留时长：超过后从架构图淡出 */
const DONE_TTL_MS = 60_000;
/** 清理间隔 */
const CLEANUP_INTERVAL_MS = 30_000;

export default function App() {
  const {
    state,
    handleSSEEvent,
    addUserMessage,
    startAssistant,
    cancelLastStream,
    setStreaming,
    giveFeedback,
    resume,
    setDatasource,
    setUserId,
    setEnableDq,
    newChat,
  } = useChat();

  const queryIdRef = useRef<string | null>(null);
  const [liveActivity, setLiveActivity] = useState<LiveActivity[]>([]);
  /** 每次新查询 +1，切回流程图 */
  const [queryPulse, setQueryPulse] = useState(0);
  const [mainTab, setMainTab] = useState<MainTabId>("arch");
  const [chatOpen, setChatOpen] = useState(true);
  const [theme, setTheme] = useState<UiTheme>(() => readUiTheme());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.body.dataset.theme = theme;
    try {
      localStorage.setItem(UI_THEME_KEY, theme);
      localStorage.setItem("db-agent-arch-theme", theme);
    } catch { /* ignore */ }
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  useEffect(() => {
    if (queryPulse > 0) setMainTab("arch");
  }, [queryPulse]);

  const upsertActivity = useCallback((node: string, status: LiveActivity["status"], task?: string) => {
    const ts = Date.now();
    setLiveActivity((prev) => [...prev.filter((a) => a.node !== node), { node, status, task, ts }]);
  }, []);

  // 定时清理已完成的节点，避免架构图越积越亮
  useEffect(() => {
    const t = window.setInterval(() => {
      const cutoff = Date.now() - DONE_TTL_MS;
      setLiveActivity((prev) => prev.filter((a) => a.status === "running" || a.ts > cutoff));
    }, CLEANUP_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, []);

  const onEvent = useCallback(
    (event: import("./types").SSEEvent) => {
      handleSSEEvent(event);
      const raw = event as unknown as Record<string, unknown>;
      const etype = (raw._eventType as string) || event.type;
      // query_id 在 connected 首包下发；interrupt 事件本身没有该字段，切勿覆盖成 null
      if (etype === "connected" && raw.query_id) {
        queryIdRef.current = String(raw.query_id);
      }
      // 实时活动 → Overview 架构图高亮
      const node = typeof event.node === "string" ? event.node : "";
      if (etype === "step_start" && node) {
        upsertActivity(node, "running", event.task);
      } else if (etype === "step_end" && node) {
        upsertActivity(node, "done", event.task);
      } else if (etype === "error") {
        setLiveActivity((prev) => prev.map((a) => (a.status === "running" ? { ...a, status: "error" } : a)));
      }
    },
    [handleSSEEvent, upsertActivity]
  );

  const onError = useCallback(
    (err: string) => {
      handleSSEEvent({
        type: "error",
        message: err.includes("Failed to fetch") || err.includes("NetworkError")
          ? "无法连接后端（请确认 :8000 已启动）"
          : err,
      } as import("./types").SSEEvent);
      setStreaming(false);
    },
    [handleSSEEvent, setStreaming]
  );

  const onDone = useCallback(() => {
    setStreaming(false);
  }, [setStreaming]);

  const { connect, abort } = useSSE({ onEvent, onError, onDone });

  const handleSend = useCallback(
    async (query: string) => {
      // 先中止上一条：UI 标取消 + 断开旧 SSE（后端 is_disconnected 后停跑）
      if (state.isStreaming) {
        cancelLastStream();
        abort();
      }
      addUserMessage(query);
      startAssistant();
      // 乐观展示：不等首包 SSE，立刻给用户反馈
      handleSSEEvent({
        type: "step_start",
        node: "connecting",
        task: "Connecting",
      } as import("./types").SSEEvent);
      queryIdRef.current = null;
      setQueryPulse((p) => p + 1);
      connect("/api/query", {
        query,
        session_id: state.sessionId,
        datasource: state.datasource,
        user_id: state.userId,
        enable_dq: state.enableDq,
      });
    },
    [state.sessionId, state.datasource, state.userId, state.enableDq, state.isStreaming, connect, abort, cancelLastStream, addUserMessage, startAssistant, handleSSEEvent]
  );

  const handleApprove = useCallback((clarified?: string) => {
    const qid = queryIdRef.current || "resume";
    resume(); // 先关弹窗
    connect("/api/query/resume", {
      query_id: qid,
      approved: true,
      session_id: state.sessionId,
      clarified_query: clarified || "",
    });
  }, [connect, resume, state.sessionId]);

  const handleReject = useCallback(() => {
    const qid = queryIdRef.current || "resume";
    resume(); // 先关弹窗
    connect("/api/query/resume", { query_id: qid, approved: false, session_id: state.sessionId });
  }, [connect, resume, state.sessionId]);

  const handleFeedback = useCallback(
    (id: string, rating: "up" | "down") => {
      giveFeedback(id, rating);
    },
    [giveFeedback]
  );

  const handleDatasourceChange = useCallback(
    (ds: string) => {
      setDatasource(ds);
    },
    [setDatasource]
  );

  const handleUserIdChange = useCallback(
    (uid: string) => {
      setUserId(uid);
    },
    [setUserId]
  );

  const handleEnableDqChange = useCallback(
    (v: boolean) => {
      setEnableDq(v);
    },
    [setEnableDq]
  );

  const handleNewChat = useCallback(() => {
    if (state.isStreaming) {
      cancelLastStream();
      abort();
    }
    setLiveActivity([]);
    queryIdRef.current = null;
    newChat();
  }, [state.isStreaming, cancelLastStream, abort, newChat]);

  return (
    <div className="app-layout" data-theme={theme}>
      <Sidebar
        activeTab={mainTab}
        onTabChange={setMainTab}
        datasource={state.datasource}
        onDatasourceChange={handleDatasourceChange}
        userId={state.userId}
        onUserIdChange={handleUserIdChange}
        enableDq={state.enableDq}
        onEnableDqChange={handleEnableDqChange}
        disabled={state.isStreaming}
        chatOpen={chatOpen}
        onToggleChat={() => setChatOpen((v) => !v)}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <main className="main-area">
        <MainTabs tab={mainTab} liveActivity={liveActivity} queryPulse={queryPulse} theme={theme} userId={state.userId} />
      </main>
      {chatOpen && (
        <ChatDock
          messages={state.messages}
          isStreaming={state.isStreaming}
          sessionId={state.sessionId}
          liveActivity={liveActivity}
          onSend={handleSend}
          onFeedback={handleFeedback}
          onNewChat={handleNewChat}
          onHide={() => setChatOpen(false)}
        />
      )}
      {state.hitlActive && state.hitlData && (
        <HITLDialog
          data={state.hitlData}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}
    </div>
  );
}

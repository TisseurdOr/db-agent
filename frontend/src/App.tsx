import { useRef, useCallback } from "react";
import { useChat } from "./hooks/useChat";
import { useSSE } from "./hooks/useSSE";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import HITLDialog from "./components/HITLDialog";
import "./App.css";

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
  } = useChat();

  const queryIdRef = useRef<string | null>(null);

  const onEvent = useCallback(
    (event: import("./types").SSEEvent) => {
      handleSSEEvent(event);
      const raw = event as unknown as Record<string, unknown>;
      const etype = (raw._eventType as string) || event.type;
      // query_id 在 connected 首包下发；interrupt 事件本身没有该字段，切勿覆盖成 null
      if (etype === "connected" && raw.query_id) {
        queryIdRef.current = String(raw.query_id);
      }
    },
    [handleSSEEvent]
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
        task: "连接服务",
      } as import("./types").SSEEvent);
      queryIdRef.current = null;
      connect("/api/query", {
        query,
        session_id: state.sessionId,
        datasource: state.datasource,
      });
    },
    [state.sessionId, state.datasource, state.isStreaming, connect, abort, cancelLastStream, addUserMessage, startAssistant, handleSSEEvent]
  );

  const handleApprove = useCallback(() => {
    const qid = queryIdRef.current || "resume";
    resume();
    connect("/api/query/resume", { query_id: qid, approved: true, session_id: state.sessionId });
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

  return (
    <div className="app-layout">
      <Sidebar datasource={state.datasource} onDatasourceChange={handleDatasourceChange} />
      <main className="main-area">
        <ChatArea
          messages={state.messages}
          isStreaming={state.isStreaming}
          sessionId={state.sessionId}
          onSend={handleSend}
          onFeedback={handleFeedback}
        />
      </main>
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

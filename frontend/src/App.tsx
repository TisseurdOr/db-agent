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
      if (event.type === "interrupt" || raw._eventType === "interrupt") {
        queryIdRef.current = raw.query_id as string || null;
      }
    },
    [handleSSEEvent]
  );

  const onError = useCallback(
    (_err: string) => {
      setStreaming(false);
    },
    [setStreaming]
  );

  const onDone = useCallback(() => {
    setStreaming(false);
  }, [setStreaming]);

  const { connect } = useSSE({ onEvent, onError, onDone });

  const handleSend = useCallback(
    async (query: string) => {
      addUserMessage(query);
      startAssistant();
      queryIdRef.current = null;
      connect("/api/query", {
        query,
        session_id: state.sessionId,
        datasource: state.datasource,
      });
    },
    [state.sessionId, state.datasource, connect, addUserMessage, startAssistant]
  );

  const handleApprove = useCallback(() => {
    const qid = queryIdRef.current;
    if (qid) {
      resume();
      connect("/api/query/resume", { query_id: qid, approved: true });
    }
  }, [connect, resume]);

  const handleReject = useCallback(() => {
    const qid = queryIdRef.current;
    if (qid) {
      connect("/api/query/resume", { query_id: qid, approved: false });
    }
  }, [connect]);

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

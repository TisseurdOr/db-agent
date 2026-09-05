import { IconChevronRight } from "./NavIcons";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import type { Message } from "../types";
import type { LiveActivity } from "./Overview";

interface Props {
  messages: Message[];
  isStreaming: boolean;
  sessionId: string;
  liveActivity: LiveActivity[];
  onSend: (query: string) => void;
  onFeedback: (id: string, rating: "up" | "down") => void;
  onNewChat: () => void;
  onHide: () => void;
}

/** 节点 → 简短动作文案（与 Overview STAGE 对应） */
const ACTION: Record<string, string> = {
  init: "Session init",
  preprocessing: "Input guardrail",
  router: "Intent routing",
  clarify: "Clarify",
  sql: "SQL query",
  hbase: "HBase query",
  hive: "Hive query",
  strategy: "Policy retrieval",
  analysis: "Analysis",
  data_quality: "Data quality scan",
  confidence_gate: "Confidence gate",
  reflection: "Reflection",
  graph: "Multi-agent graph",
  guardrail: "Guardrail block",
  connecting: "Connecting",
};

export default function ChatDock({
  messages,
  isStreaming,
  sessionId,
  liveActivity,
  onSend,
  onFeedback,
  onNewChat,
  onHide,
}: Props) {
  const latest = liveActivity.length > 0 ? liveActivity[liveActivity.length - 1] : null;
  const statusText = latest
    ? `${ACTION[latest.node] ?? latest.node}${
        latest.status === "running" ? " · running" : latest.status === "error" ? " · error" : " · done"
      }`
    : null;

  return (
    <aside className="chat-dock">
      <div className="chat-dock-header">
        <span className="chat-dock-title-text">Chat</span>
        {statusText && (
          <span className={`chat-dock-status ${latest?.status ?? ""}`} title={latest?.task}>
            {statusText}
          </span>
        )}
        <button
          type="button"
          className="chat-dock-new"
          onClick={onNewChat}
          title="Start a new thread"
        >
          New chat
        </button>
        <button
          type="button"
          className="chat-dock-toggle"
          onClick={onHide}
          title="Hide chat (reopen from the sidebar)"
        >
          <IconChevronRight />
        </button>
      </div>

      <div className="chat-dock-messages">
        {messages.length === 0 && (
          <div className="chat-dock-empty">
            <div style={{ fontSize: 12, color: "#888", lineHeight: 1.7 }}>
              New thread ready.
              <br />
              Ask here; the architecture view lights up live.
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            sessionId={sessionId}
            onFeedback={onFeedback}
          />
        ))}
      </div>
      <ChatInput onSend={onSend} disabled={isStreaming} />
    </aside>
  );
}

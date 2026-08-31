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
  init: "初始化会话",
  preprocessing: "输入护栏检查",
  router: "意图路由",
  clarify: "需求澄清",
  sql: "SQL 查询",
  hbase: "HBase 查询",
  hive: "Hive 查询",
  strategy: "制度检索",
  analysis: "综合分析",
  data_quality: "数据质量扫描",
  confidence_gate: "置信度门控",
  reflection: "质量反思",
  graph: "多 Agent 编排",
  guardrail: "护栏拦截",
  connecting: "连接服务",
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
    ? `${latest.status === "running" ? "🔶" : latest.status === "error" ? "❌" : "✅"} ${
        ACTION[latest.node] ?? latest.node
      }${latest.status === "running" ? " 进行中…" : " 已完成"}`
    : null;

  return (
    <aside className="chat-dock">
      <div className="chat-dock-header">
        <span className="chat-dock-title-text">💬 Chat</span>
        {statusText && (
          <span className={`chat-dock-status ${latest?.status ?? ""}`} title={latest?.task}>
            {statusText}
          </span>
        )}
        <button
          type="button"
          className="chat-dock-new"
          onClick={onNewChat}
          title="开启新对话（新 thread）"
        >
          新对话
        </button>
        <button
          type="button"
          className="chat-dock-toggle"
          onClick={onHide}
          title="隐藏聊天（可在左侧 db-agent 旁重新打开）"
        >
          ▶
        </button>
      </div>

      <div className="chat-dock-messages">
        {messages.length === 0 && (
          <div className="chat-dock-empty">
            <div style={{ fontSize: 22, marginBottom: 6 }}>🤖</div>
            <div style={{ fontSize: 12, color: "#888", lineHeight: 1.7 }}>
              新对话已就绪
              <br />
              在这里提问，左侧流程图会实时点亮。
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

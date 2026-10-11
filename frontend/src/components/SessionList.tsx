/* 会话列表 —— 挂在右侧对话框上，点标题栏的箭头展开。
 *
 * 数据源：GET /api/sessions（标题 / 条数 / 更新时间，按最近更新倒序）。
 * 点一条 → 父组件去拉该会话的完整消息并切过去。
 */
import type { SessionSummary } from "../types";

interface Props {
  sessions: SessionSummary[];
  currentId: string;
  loading: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}

/** ISO 时间 → 相对描述（今天 14:03 / 昨天 / 10-08） */
function relTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (sameDay) return hhmm;
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "昨天";
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function SessionList({
  sessions,
  currentId,
  loading,
  onSelect,
  onDelete,
  onNew,
}: Props) {
  return (
    <div className="session-list">
      <div className="session-list-head">
        <span className="session-list-title">History</span>
        <button type="button" className="session-list-new" onClick={onNew} title="Start a new thread">
          + New
        </button>
      </div>

      <div className="session-list-body">
        {loading && sessions.length === 0 && (
          <div className="session-list-empty">Loading…</div>
        )}
        {!loading && sessions.length === 0 && (
          <div className="session-list-empty">
            No saved threads yet.
            <br />
            Ask something — it lands here.
          </div>
        )}

        {sessions.map((s) => {
          const active = s.id === currentId;
          return (
            <div
              key={s.id}
              className={`session-item${active ? " active" : ""}`}
              onClick={() => onSelect(s.id)}
              title={s.title}
            >
              <div className="session-item-main">
                <div className="session-item-title">{s.title || s.id}</div>
                <div className="session-item-meta">
                  {s.message_count} msg
                  {relTime(s.updated_at) ? ` · ${relTime(s.updated_at)}` : ""}
                </div>
              </div>
              <button
                type="button"
                className="session-item-del"
                title="Delete this thread"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s.id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

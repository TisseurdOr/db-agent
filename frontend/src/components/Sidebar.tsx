import DataSourceSelector from "./DataSourceSelector";
import RoleSelector from "./RoleSelector";
import DqToggle from "./DqToggle";

export type MainTabId = "arch" | "nodes" | "queries" | "dashboard" | "database" | "memory";

const NAV: { id: MainTabId; label: string; icon: string }[] = [
  { id: "arch", label: "Architecture", icon: "🗺️" },
  { id: "nodes", label: "Node stats", icon: "📊" },
  { id: "queries", label: "Recent queries", icon: "🕘" },
  { id: "dashboard", label: "Dashboard", icon: "📈" },
  { id: "memory", label: "Memory", icon: "🧠" },
  { id: "database", label: "Database", icon: "🗄️" },
];

interface Props {
  activeTab: MainTabId;
  onTabChange: (tab: MainTabId) => void;
  datasource: string;
  onDatasourceChange: (datasource: string) => void;
  userId: string;
  onUserIdChange: (userId: string) => void;
  enableDq: boolean;
  onEnableDqChange: (v: boolean) => void;
  disabled?: boolean;
  chatOpen: boolean;
  onToggleChat: () => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
}

export default function Sidebar({
  activeTab,
  onTabChange,
  datasource,
  onDatasourceChange,
  userId,
  onUserIdChange,
  enableDq,
  onEnableDqChange,
  disabled,
  chatOpen,
  onToggleChat,
  theme,
  onToggleTheme,
}: Props) {
  return (
    <aside className="app-sidebar">
      <div className="app-sidebar-brand">
        <div>
          <h2 className="app-sidebar-title">🤖 db-agent</h2>
          <p className="app-sidebar-sub">AI data analysis agent</p>
        </div>
        <div className="app-sidebar-brand-actions">
          <button
            type="button"
            className="app-sidebar-theme-toggle"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Light background" : "Dark background"}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
          <button
            type="button"
            className={`app-sidebar-chat-toggle ${chatOpen ? "on" : ""}`}
            onClick={onToggleChat}
            title={chatOpen ? "Hide chat" : "Show chat"}
            aria-label={chatOpen ? "Hide chat" : "Show chat"}
            aria-pressed={chatOpen}
          >
            {chatOpen ? "▶" : "◀"}
          </button>
        </div>
      </div>

      <nav className="app-sidebar-nav" aria-label="Main views">
        <div className="app-sidebar-section">Views</div>
        {NAV.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`app-sidebar-link ${activeTab === item.id ? "on" : ""}`}
            onClick={() => onTabChange(item.id)}
          >
            <span>{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="app-sidebar-section">Settings</div>
      <DataSourceSelector datasource={datasource} onChange={onDatasourceChange} />
      <RoleSelector userId={userId} onChange={onUserIdChange} disabled={disabled} />
      <DqToggle enabled={enableDq} onChange={onEnableDqChange} disabled={disabled} />

      <div className="app-sidebar-model">
        <div className="app-sidebar-section">Model</div>
        <div>DeepSeek Chat</div>
      </div>
    </aside>
  );
}

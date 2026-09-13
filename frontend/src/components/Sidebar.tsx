import { useCallback, useEffect, useState } from "react";
import DataSourceSelector from "./DataSourceSelector";
import RoleSelector from "./RoleSelector";
import DqToggle from "./DqToggle";
import type { RbacUser } from "../types";
import {
  IconBarChart,
  IconBot,
  IconChevronLeft,
  IconChevronRight,
  IconClock,
  IconCpu,
  IconDatabase,
  IconGitBranch,
  IconLayers,
  IconLineChart,
  IconMoon,
  IconSun,
} from "./NavIcons";

export type MainTabId = "arch" | "nodes" | "queries" | "dashboard" | "database" | "lineage" | "memory" | "ops" | "eval";

const NAV: { id: MainTabId; label: string; icon: typeof IconLayers }[] = [
  { id: "arch", label: "Architecture", icon: IconLayers },
  { id: "nodes", label: "Node stats", icon: IconBarChart },
  { id: "queries", label: "Recent queries", icon: IconClock },
  { id: "dashboard", label: "Dashboard", icon: IconLineChart },
  { id: "memory", label: "Memory", icon: IconCpu },
  { id: "database", label: "Database", icon: IconDatabase },
  { id: "lineage", label: "Lineage", icon: IconGitBranch },
  { id: "ops", label: "Ops metrics", icon: IconLineChart },
  { id: "eval", label: "Eval results", icon: IconBarChart },
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
  // viewer/support 关库；其余先乐观打开，等 RoleSelector 回调校准
  const [canAccessDatabase, setCanAccessDatabase] = useState(
    () => userId !== "viewer" && userId !== "support",
  );
  // Dashboard：仅 dba / analyst（部门经理不可）
  const [canAccessDashboard, setCanAccessDashboard] = useState(
    () => userId === "dba" || userId === "analyst",
  );

  useEffect(() => {
    setCanAccessDatabase(userId !== "viewer" && userId !== "support");
    setCanAccessDashboard(userId === "dba" || userId === "analyst");
  }, [userId]);

  const handleDatabaseAccessChange = useCallback(
    (canAccess: boolean, _user: RbacUser | null) => {
      setCanAccessDatabase(canAccess);
      if (!canAccess && activeTab === "database") {
        onTabChange("arch");
      }
    },
    [activeTab, onTabChange],
  );

  const handleDashboardAccessChange = useCallback(
    (canAccess: boolean, _user: RbacUser | null) => {
      setCanAccessDashboard(canAccess);
      if (!canAccess && activeTab === "dashboard") {
        onTabChange("arch");
      }
    },
    [activeTab, onTabChange],
  );

  // 保险：权限关掉后若仍停在受限页，强制离开
  useEffect(() => {
    if (!canAccessDatabase && (activeTab === "database" || activeTab === "lineage")) {
      onTabChange("arch");
    }
    if (!canAccessDashboard && activeTab === "dashboard") {
      onTabChange("arch");
    }
  }, [canAccessDatabase, canAccessDashboard, activeTab, onTabChange]);

  const navItems = NAV.filter((item) => {
    if (item.id === "database" || item.id === "lineage") return canAccessDatabase;
    if (item.id === "dashboard") return canAccessDashboard;
    return true;
  });

  return (
    <aside className="app-sidebar">
      <div className="app-sidebar-brand">
        <div className="app-sidebar-brand-text">
          <h2 className="app-sidebar-title">
            <IconBot className="app-sidebar-brand-icon" />
            db-agent
          </h2>
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
            {theme === "dark" ? <IconSun /> : <IconMoon />}
          </button>
          <button
            type="button"
            className={`app-sidebar-chat-toggle ${chatOpen ? "on" : ""}`}
            onClick={onToggleChat}
            title={chatOpen ? "Hide chat" : "Show chat"}
            aria-label={chatOpen ? "Hide chat" : "Show chat"}
            aria-pressed={chatOpen}
          >
            {chatOpen ? <IconChevronRight /> : <IconChevronLeft />}
          </button>
        </div>
      </div>

      <nav className="app-sidebar-nav" aria-label="Main views">
        <div className="app-sidebar-section">Views</div>
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={`app-sidebar-link ${activeTab === item.id ? "on" : ""}`}
              onClick={() => onTabChange(item.id)}
            >
              <span className="app-sidebar-link-icon"><Icon /></span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="app-sidebar-section">Settings</div>
      <RoleSelector
        userId={userId}
        onChange={onUserIdChange}
        onDatabaseAccessChange={handleDatabaseAccessChange}
        onDashboardAccessChange={handleDashboardAccessChange}
        disabled={disabled}
      />
      <DqToggle enabled={enableDq} onChange={onEnableDqChange} disabled={disabled} />

      <div className="app-sidebar-section">Data source</div>
      <DataSourceSelector datasource={datasource} onChange={onDatasourceChange} />

      <div className="app-sidebar-model">
        <div className="app-sidebar-section">Model</div>
        <div>DeepSeek Chat</div>
      </div>
    </aside>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import type { RbacUser } from "../types";
import { loadPersistedUserId } from "../rbacStorage";

interface Props {
  userId: string;
  onChange: (userId: string) => void;
  /** 每次切换身份 / 用户列表加载后，立刻回调当前是否可访问 Database */
  onDatabaseAccessChange?: (canAccess: boolean, user: RbacUser | null) => void;
  /** Dashboard 仅 dba / analyst */
  onDashboardAccessChange?: (canAccess: boolean, user: RbacUser | null) => void;
  disabled?: boolean;
}

/** 通用角色排前面；经理单独一组（每人对应不同部门，用于演示行级隔离） */
const GENERAL_ROLES = new Set(["dba", "analyst", "viewer", "support"]);

/** API 未带回字段时的本地兜底：viewer / support 关库 */
function resolveDbAccess(u: RbacUser | null | undefined): boolean {
  if (!u) return false;
  if (typeof u.can_access_database === "boolean") return u.can_access_database;
  return u.role !== "viewer" && u.role !== "support";
}

function resolveDashboardAccess(u: RbacUser | null | undefined): boolean {
  if (!u) return false;
  if (typeof u.can_access_dashboard === "boolean") return u.can_access_dashboard;
  return u.role === "dba" || u.role === "analyst";
}

function managerLabel(u: RbacUser, revealName: boolean): string {
  const dept = u.dept_name || (u.dept_id != null ? `Dept ${u.dept_id}` : "Unknown dept");
  if (revealName) return `${u.name} · ${dept}`;
  return `${dept} manager`;
}

export default function RoleSelector({ userId, onChange, onDatabaseAccessChange, onDashboardAccessChange, disabled }: Props) {
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [managerHint, setManagerHint] = useState(
    "DBA / managers / analysts can access the DB; managers get row-level isolation on employees",
  );

  const revealManagerNames = userId === "dba";

  const emitAccess = useCallback(
    (uid: string, list: RbacUser[]) => {
      const u = list.find((x) => x.id === uid) ?? null;
      onDatabaseAccessChange?.(resolveDbAccess(u), u);
      onDashboardAccessChange?.(resolveDashboardAccess(u), u);
    },
    [onDatabaseAccessChange, onDashboardAccessChange],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch("/api/rbac");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (cancelled) return;
        const list = (data.users || []) as RbacUser[];
        setUsers(list);
        if (data.hint?.manager) setManagerHint(data.hint.manager);
        setError("");

        // localStorage 为刷新后的权威来源（避免空 select 回落成 viewer 盖掉已选身份）
        const persisted = loadPersistedUserId();
        const preferred = list.some((u) => u.id === persisted)
          ? persisted
          : list.some((u) => u.id === userId)
            ? userId
            : (list[0]?.id || "viewer");
        if (preferred !== userId) {
          onChange(preferred);
        }
        emitAccess(preferred, list);
      } catch {
        if (cancelled) return;
        setError("Failed to load roles");
        const fallback: RbacUser[] = [
          { id: "dba", name: "研发DBA", role: "dba", role_name: "研发DBA", can_access_database: true, can_access_dashboard: true },
          { id: "analyst", name: "数据分析师", role: "analyst", role_name: "数据分析师", can_access_database: true, can_access_dashboard: true },
          { id: "zhoufang", name: "周芳", role: "manager", role_name: "部门经理", dept_id: 1, dept_name: "销售部", can_access_database: true, can_access_dashboard: false },
          { id: "xiaoyiming", name: "萧一鸣", role: "manager", role_name: "部门经理", dept_id: 2, dept_name: "市场部", can_access_database: true, can_access_dashboard: false },
          { id: "viewer", name: "访客", role: "viewer", role_name: "访客", can_access_database: false, can_access_dashboard: false },
          { id: "support", name: "技术支持", role: "support", role_name: "技术支持", can_access_database: false, can_access_dashboard: false },
        ];
        setUsers(fallback);
        const persisted = loadPersistedUserId();
        const preferred = fallback.some((u) => u.id === persisted)
          ? persisted
          : fallback.some((u) => u.id === userId)
            ? userId
            : "viewer";
        if (preferred !== userId) onChange(preferred);
        emitAccess(preferred, fallback);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!loaded || users.length === 0) return;
    emitAccess(userId, users);
  }, [userId, users, loaded, emitAccess]);

  const { general, managers } = useMemo(() => {
    const g: RbacUser[] = [];
    const m: RbacUser[] = [];
    for (const u of users) {
      if (u.role === "manager") m.push(u);
      else if (GENERAL_ROLES.has(u.role)) g.push(u);
      else g.push(u);
    }
    m.sort((a, b) => (a.dept_id ?? 0) - (b.dept_id ?? 0));
    return { general: g, managers: m };
  }, [users]);

  const current = users.find((u) => u.id === userId) ?? null;
  const isManager = current?.role === "manager";
  const canAccessDb = resolveDbAccess(current);
  const canAccessDash = resolveDashboardAccess(current);

  const handleSelect = (nextId: string) => {
    onChange(nextId);
    emitAccess(nextId, users);
  };

  return (
    <div className="side-ctl">
      <div className="side-ctl-label">RBAC identity</div>
      {!loaded ? (
        <div className="side-ctl-hint">Loading roles…</div>
      ) : (
        <select
          className="side-select"
          value={userId}
          disabled={disabled}
          onChange={(e) => handleSelect(e.target.value)}
        >
          <optgroup label="Roles">
            {general.map((u) => (
              <option key={u.id} value={u.id}>
                {u.role_name}（{u.id}）
              </option>
            ))}
          </optgroup>
          {managers.length > 0 && (
            <optgroup label="Managers · row-level by dept">
              {managers.map((u) => (
                <option key={u.id} value={u.id}>
                  {managerLabel(u, revealManagerNames)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      )}

      {current && (
        <div className="side-ctl-hint">
          Current: <span className="side-accent">{current.role_name}</span>
          {isManager && current.dept_name && (
            <> · <span className="side-good">{current.dept_name}</span></>
          )}
          {isManager && revealManagerNames && (
            <> · <span className="side-muted">{current.name}</span></>
          )}
          <> · Database: <span className={canAccessDb ? "side-good" : "side-muted"}>{canAccessDb ? "on" : "off"}</span></>
          <> · Dashboard: <span className={canAccessDash ? "side-good" : "side-muted"}>{canAccessDash ? "on" : "off"}</span></>
        </div>
      )}
      {isManager && <div className="side-ctl-hint warn">{managerHint}</div>}
      {!revealManagerNames && managers.length > 0 && (
        <div className="side-ctl-hint">
          Manager real names visible to DBA only — switch to DBA to reveal
        </div>
      )}
      {error && <div className="side-ctl-hint warn">{error} (using local fallback)</div>}
    </div>
  );
}

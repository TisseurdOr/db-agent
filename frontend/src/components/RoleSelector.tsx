import { useEffect, useMemo, useState } from "react";
import type { RbacUser } from "../types";

interface Props {
  userId: string;
  onChange: (userId: string) => void;
  disabled?: boolean;
}

/** 通用角色排前面；经理单独一组（每人对应不同部门，用于演示行级隔离） */
const GENERAL_ROLES = new Set(["dba", "analyst", "viewer", "support"]);

function managerLabel(u: RbacUser, revealName: boolean): string {
  const dept = u.dept_name || (u.dept_id != null ? `Dept ${u.dept_id}` : "Unknown dept");
  if (revealName) return `${u.name} · ${dept}`;
  return `${dept} manager`;
}

export default function RoleSelector({ userId, onChange, disabled }: Props) {
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [error, setError] = useState("");
  const [managerHint, setManagerHint] = useState(
    "Managers get row-level isolation: employees queries auto-add WHERE dept_id=own dept",
  );

  const revealManagerNames = userId === "dba";

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch("/api/rbac");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (cancelled) return;
        setUsers(data.users || []);
        if (data.hint?.manager) setManagerHint(data.hint.manager);
        setError("");
      } catch {
        if (cancelled) return;
        setError("Failed to load roles");
        setUsers([
          { id: "dba", name: "研发DBA", role: "dba", role_name: "研发DBA" },
          { id: "analyst", name: "数据分析师", role: "analyst", role_name: "数据分析师" },
          { id: "zhoufang", name: "周芳", role: "manager", role_name: "部门经理", dept_id: 1, dept_name: "销售部" },
          { id: "xiaoyiming", name: "萧一鸣", role: "manager", role_name: "部门经理", dept_id: 2, dept_name: "市场部" },
          { id: "viewer", name: "访客", role: "viewer", role_name: "访客" },
          { id: "support", name: "技术支持", role: "support", role_name: "技术支持" },
        ]);
      }
    })();
    return () => { cancelled = true; };
  }, []);

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

  const current = users.find((u) => u.id === userId);
  const isManager = current?.role === "manager";

  return (
    <div className="side-ctl">
      <div className="side-ctl-label">RBAC identity</div>
      <select
        className="side-select"
        value={userId}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
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

      {current && (
        <div className="side-ctl-hint">
          Current: <span className="side-accent">{current.role_name}</span>
          {isManager && current.dept_name && (
            <> · <span className="side-good">{current.dept_name}</span></>
          )}
          {isManager && revealManagerNames && (
            <> · <span className="side-muted">{current.name}</span></>
          )}
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

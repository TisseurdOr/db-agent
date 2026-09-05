/** RBAC identity persistence — shared by useChat / RoleSelector / DatabaseBrowser. */

export const RBAC_USER_KEY = "db-agent:rbac-user-id";

/** Demo-friendly default: DBA can open Database; viewer would look "broken". */
export const DEFAULT_RBAC_USER_ID = "dba";

export function loadPersistedUserId(): string {
  try {
    const v = localStorage.getItem(RBAC_USER_KEY);
    if (v && /^[a-zA-Z0-9_-]+$/.test(v)) return v;
  } catch {
    /* private mode / SSR */
  }
  return DEFAULT_RBAC_USER_ID;
}

export function persistUserId(userId: string) {
  try {
    localStorage.setItem(RBAC_USER_KEY, userId);
  } catch {
    /* ignore quota / private mode */
  }
}

"use client";
/** Client-side auth helpers: token storage, JWT decode, role permissions. */

export type Role = "viewer" | "executive" | "supervisor" | "admin" | "super_admin";

const RANK: Record<Role, number> = { viewer: 0, executive: 1, supervisor: 2, admin: 3, super_admin: 4 };
const TOKEN_KEY = "sb_token";

export interface AuthUser {
  username: string;
  name: string;
  role: Role;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
}

export function decodeUser(): AuthUser | null {
  const token = getToken();
  if (!token) return null;
  try {
    const [, payload] = token.split(".");
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    if (json.exp && json.exp * 1000 < Date.now()) {
      clearToken();
      return null;
    }
    return { username: json.username, name: json.name, role: json.role as Role };
  } catch {
    return null;
  }
}

export function roleAtLeast(role: Role | undefined, min: Role): boolean {
  if (!role) return false;
  return (RANK[role] ?? 0) >= RANK[min];
}

/** Permission → minimum role required. */
export const PERMISSION_MIN: Record<string, Role> = {
  "ticket:write": "executive",
  "exec:manage": "supervisor",
  "kb:reload": "supervisor",
  "settings:write": "admin",
  "users:manage": "super_admin",
};

export function can(role: Role | undefined, action: keyof typeof PERMISSION_MIN): boolean {
  return roleAtLeast(role, PERMISSION_MIN[action]);
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "Viewer",
  executive: "Executive",
  supervisor: "Supervisor",
  admin: "Admin",
  super_admin: "Super Admin",
};

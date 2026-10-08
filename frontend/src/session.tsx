import { createContext, useContext } from "react";
import type { Me, Permission } from "./api";

export const Session = createContext<Me | null>(null);

/** Whether the signed-in member may do something. The API enforces the same rule; this only hides what would fail. */
export function useCan() {
  const me = useContext(Session);
  return (p: Permission) => !!me?.permissions.includes(p);
}

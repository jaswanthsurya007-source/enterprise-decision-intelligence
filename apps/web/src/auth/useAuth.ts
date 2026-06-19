import { useContext } from "react";
import { AuthContext, type AuthState } from "./AuthProvider";

/** Access the dev-auth context. Throws if used outside `<AuthProvider>`. */
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}

/** Convenience: the configured gateway client with the bearer wired. */
export function useApiClient() {
  return useAuth().client;
}

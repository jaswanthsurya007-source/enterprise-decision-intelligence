/**
 * App-level error boundary. Catches render-time errors in a subtree and shows a
 * calm, professional fallback (not a white screen). Network/API errors are
 * handled per-query by TanStack Query; this is the last-resort guard for
 * unexpected render crashes.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Hook point for OTel/logging (lib/otel) — kept side-effect-light here.
    if (import.meta.env.DEV) {
      console.error("[ErrorBoundary]", error, info.componentStack);
    }
  }

  reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="card card-pad max-w-md text-center">
          <div className="mb-2 text-sm font-semibold text-status-critical">
            Something went wrong
          </div>
          <p className="mb-4 text-sm text-fg-muted">
            The dashboard hit an unexpected error while rendering. Your data is
            unaffected.
          </p>
          <pre className="mb-4 max-h-32 overflow-auto rounded bg-surface-inset p-2 text-left text-2xs text-fg-subtle">
            {error.message}
          </pre>
          <button
            type="button"
            onClick={this.reset}
            className="focus-ring rounded-md border border-border-strong bg-surface-overlay px-3 py-1.5 text-sm text-fg-default hover:bg-surface-overlay/70"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}

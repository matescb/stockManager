import React from "react";

/**
 * ChunkLoadErrorBoundary (FE2-022)
 *
 * Catches `ChunkLoadError` (Vite / webpack) that fires when a JS chunk
 * hash changes between deploys and the browser tries to fetch the old URL.
 *
 * Recovery strategy:
 *  1. First failure at a given pathname → store a flag in sessionStorage
 *     and reload the page. The reload fetches the new manifest and the
 *     updated chunk URL.
 *  2. Second failure at the same pathname → the flag is already set,
 *     meaning the reload didn't help (genuine network failure or the
 *     chunk is actually broken). Fall through to a friendly retry banner
 *     so the user isn't stuck in an infinite reload loop.
 *
 * Non-chunk errors are re-thrown so the outer Sentry.ErrorBoundary still
 * captures them.
 */

interface Props {
  children: React.ReactNode;
}

interface State {
  /**
   * Set to the caught error for non-chunk errors. We re-throw in
   * componentDidCatch, but getDerivedStateFromError must return a state
   * update (never null) so React stops trying to re-render the children
   * before the outer boundary catches it.
   */
  nonChunkError: unknown | null;
  /**
   * Set when a chunk error was caught and we're waiting for the reload
   * (first attempt) or showing the retry banner (second attempt).
   */
  chunkErrorCaught: boolean;
  /** true once we've seen a chunk error and the reload didn't help */
  showRetryBanner: boolean;
}

export function isChunkLoadError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  return (
    err.name === "ChunkLoadError" ||
    /Loading chunk \d+ failed/i.test(err.message) ||
    /Failed to fetch dynamically imported module/i.test(err.message)
  );
}

export function reloadAttemptKey(pathname: string) {
  return `chunkReloadAttempt:${pathname}`;
}

/**
 * Inner helper that clears the per-path reload-attempt flag once the
 * chunk has loaded successfully. This must be a separate component so
 * its componentDidMount only fires when no error has occurred — putting
 * this logic directly on ChunkLoadErrorBoundary.componentDidMount would
 * clear the flag before the boundary even gets a chance to react to an
 * error thrown by its children.
 */
class SuccessGuard extends React.Component<{
  pathname: string;
  children: React.ReactNode;
}> {
  componentDidMount() {
    sessionStorage.removeItem(reloadAttemptKey(this.props.pathname));
  }
  render() {
    return this.props.children;
  }
}

export class ChunkLoadErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      nonChunkError: null,
      chunkErrorCaught: false,
      showRetryBanner: false,
    };
  }

  /**
   * Called synchronously during rendering to derive state from the error.
   * Must return a state update (never null) to prevent React from
   * re-rendering the error-throwing subtree.
   */
  static getDerivedStateFromError(err: unknown): Partial<State> {
    if (!isChunkLoadError(err)) {
      // Non-chunk error: record it. componentDidCatch will re-throw.
      return { nonChunkError: err };
    }
    // Chunk error: mark as caught so we don't render children again.
    return { chunkErrorCaught: true };
  }

  componentDidCatch(err: unknown) {
    if (!isChunkLoadError(err)) {
      // Re-throw so the outer Sentry boundary sees it.
      throw err;
    }

    const key = reloadAttemptKey(window.location.pathname);
    if (!sessionStorage.getItem(key)) {
      // First attempt: flag + reload.
      sessionStorage.setItem(key, "1");
      window.location.reload();
    } else {
      // Second attempt: reload didn't help — show the retry banner.
      this.setState({ showRetryBanner: true });
    }
  }

  render() {
    if (this.state.showRetryBanner) {
      return (
        <div className="p-6 flex flex-col gap-3 items-start">
          <p className="text-sm text-muted">
            This section failed to load. It may be a temporary network issue.
          </p>
          <button
            className="btn btn-primary"
            onClick={() => {
              const key = reloadAttemptKey(window.location.pathname);
              sessionStorage.removeItem(key);
              window.location.reload();
            }}
          >
            Retry
          </button>
        </div>
      );
    }

    // chunkErrorCaught without showRetryBanner: we're about to reload;
    // render nothing while the page reloads.
    if (this.state.chunkErrorCaught) {
      return null;
    }

    // nonChunkError: the error was re-thrown in componentDidCatch,
    // the outer boundary takes over. Render nothing here.
    if (this.state.nonChunkError !== null) {
      return null;
    }

    // No error: wrap children in SuccessGuard to clear the reload flag
    // once the subtree mounts cleanly.
    return (
      <SuccessGuard pathname={window.location.pathname}>
        {this.props.children}
      </SuccessGuard>
    );
  }
}

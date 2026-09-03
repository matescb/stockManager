import React from "react";

/**
 * Keeps a broken preview from taking the CAD tab with it.
 *
 * KiCanvas is alpha software (see `docs/frontend/kicanvas-provenance.md`) and
 * it parses files this app did not write — a symbol uploaded from any
 * vendor, in any dialect of the format. A parse failure there is a
 * normal outcome, not an exceptional one, and the tab it lives on is
 * where users configure the very entry that failed. Losing the form
 * because the picture didn't draw would be the worse bug.
 *
 * Deliberately NOT re-thrown to the outer Sentry boundary, unlike
 * `ChunkLoadErrorBoundary`: there is no action to take on "an alpha
 * viewer could not draw this symbol", and one malformed upload would
 * otherwise generate an event per render.
 */

interface Props {
  /** Changing this resets the boundary — a new entry deserves a new try. */
  resetKey: string;
  children: React.ReactNode;
}

interface State {
  failed: boolean;
  resetKey: string;
}

export class PreviewBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { failed: false, resetKey: props.resetKey };
  }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) {
      return { failed: false, resetKey: props.resetKey };
    }
    return null;
  }

  render() {
    if (this.state.failed) return <PreviewUnavailable />;
    return this.props.children;
  }
}

export function PreviewUnavailable({
  message = "Preview unavailable",
}: {
  message?: string;
}) {
  return (
    <div
      role="status"
      className="flex h-full items-center justify-center p-4 text-center text-sm text-muted"
    >
      {message}
    </div>
  );
}

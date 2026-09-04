import React from "react";

/**
 * Keeps a broken preview from taking the CAD tab with it.
 *
 * The 3D model viewer (`ModelPreview`) renders through three.js/WebGL and
 * parses model files this app did not write — a STEP or WRL from any
 * vendor. A parse or WebGL failure there is a normal outcome, not an
 * exceptional one, and the tab it lives on is where users configure the
 * very entry that failed. Losing the form because the picture didn't draw
 * would be the worse bug.
 *
 * Deliberately NOT re-thrown to the outer Sentry boundary, unlike
 * `ChunkLoadErrorBoundary`: there is no action to take on "a viewer could
 * not draw this model", and one malformed upload would otherwise generate
 * an event per render.
 *
 * The 2D symbol/footprint previews render server-side and are shown via an
 * `<img>` (see `SvgPreview`), so they degrade on `onError` and do not need
 * this boundary.
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

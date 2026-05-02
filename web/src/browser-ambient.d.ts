/**
 * Ambient type extensions for browser APIs not yet reflected in TypeScript's
 * DOM lib. These are real, shipped browser APIs; we declare them here rather
 * than using `as any` at every call site.
 */

// --- WebAudio: webkit-prefixed AudioContext (Safari / older browsers) ------
interface Window {
  /** Safari / older Chrome fallback for the standard `AudioContext`. */
  webkitAudioContext?: typeof AudioContext;
}

// --- MediaTrack: `zoom` capability (Chrome on Android, some desktop) -------
// TypeScript's DOM lib ships `MediaTrackSettings.zoom?: number` (read-back)
// but omits `MediaTrackCapabilities.zoom` (capability range query).
// The MDN-documented shape is a `DoubleRange` with min/max/step.
interface MediaTrackCapabilities {
  zoom?: DoubleRange & { step?: number };
}

// `MediaTrackConstraintSet` also doesn't include zoom, so `applyConstraints`
// rejects `{ zoom }` values without a cast.  Extend it here.
interface MediaTrackConstraintSet {
  zoom?: ConstrainDouble;
}

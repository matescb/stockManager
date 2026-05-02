/**
 * Scaffolded contract tests for the `useEntityForm` reset hook
 * (FE-004). The hook does not exist yet — these tests are
 * `it.skip(...)` placeholders that document the contract for whoever
 * lands the hook. Unskip them in the same PR that introduces the hook.
 *
 * Lives in `__dom__/` because once the hook lands the tests will
 * render with `<RenderHook>`-style setup, which needs jsdom.
 */
import { describe, it } from "vitest";

describe("useEntityForm (contract — pending FE-004)", () => {
  it.skip("resets form state when the entity id changes", () => {
    // Render the hook with `entity={a}`, mutate field values, then
    // re-render with `entity={b}`. Field values must reset to b's
    // initial state, not carry over from a.
  });

  it.skip("preserves form state when the entity object identity changes but id is stable", () => {
    // A refetch that returns a new object reference for the same id
    // must NOT clobber in-flight user edits.
  });

  it.skip("clears the form when entity becomes null (creating a new one)", () => {
    // Toggling from edit mode (entity={a}) to create mode (entity=null)
    // resets to defaultValues.
  });
});

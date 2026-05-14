/**
 * Imperative confirm + prompt primitives.
 *
 * Replaces every `window.confirm(...)` / `window.prompt(...)` call site
 * in the app (FE HIGH-5 in the 2026-04-30 review). Native browser dialogs
 * are not styled, look like phishing prompts on most platforms, are not
 * keyboard-accessible on mobile Safari, and block the event loop. This
 * file provides:
 *
 *   const confirm = useConfirm();
 *   if (!await confirm({ message: "Delete this entry?", severity: "danger" })) return;
 *
 *   const prompt = usePrompt();
 *   const name = await prompt({ message: "Preset name?", defaultValue: "" });
 *   if (name === null) return;
 *
 * Identical call shape to the natives, just async. The provider mounts
 * once at the top of `<App>`; the hooks read its imperative methods
 * out of context.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Modal } from "./Modal";

type Severity = "default" | "danger" | "warning";

type ConfirmOptions = {
  message: string;
  /** Title above the message. Optional — defaults to "Confirm" / "Delete". */
  title?: string;
  /** Label on the OK button. Optional — defaults vary by severity. */
  confirmLabel?: string;
  /** Label on the Cancel button. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Tints the OK button. `danger` = red, `warning` = amber. */
  severity?: Severity;
};

type PromptOptions = {
  message: string;
  title?: string;
  defaultValue?: string;
  /** Label on the OK button. Defaults to "OK". */
  confirmLabel?: string;
  /** Label on the Cancel button. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Optional placeholder for the input. */
  placeholder?: string;
};

type ConfirmCtx = {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
};

const Ctx = createContext<ConfirmCtx | null>(null);

type DialogState =
  | { kind: "none" }
  | { kind: "confirm"; opts: ConfirmOptions; resolve: (v: boolean) => void }
  | { kind: "prompt"; opts: PromptOptions; resolve: (v: string | null) => void };

export function ConfirmDialogProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DialogState>({ kind: "none" });
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset prompt input whenever a new prompt opens.
  useEffect(() => {
    if (state.kind === "prompt") {
      setInputValue(state.opts.defaultValue ?? "");
      // Focus + select-all on next paint so the user can just type.
      queueMicrotask(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [state]);

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      // If something else is already open, drop it (resolve false) and
      // replace. Avoids nesting prompts; in practice this doesn't fire.
      setState((prev) => {
        if (prev.kind === "confirm") prev.resolve(false);
        if (prev.kind === "prompt") prev.resolve(null);
        return { kind: "confirm", opts, resolve };
      });
    });
  }, []);

  const prompt = useCallback((opts: PromptOptions) => {
    return new Promise<string | null>((resolve) => {
      setState((prev) => {
        if (prev.kind === "confirm") prev.resolve(false);
        if (prev.kind === "prompt") prev.resolve(null);
        return { kind: "prompt", opts, resolve };
      });
    });
  }, []);

  const cancelActiveDialog = useCallback(() => {
    setState((prev) => {
      if (prev.kind === "confirm") prev.resolve(false);
      if (prev.kind === "prompt") prev.resolve(null);
      return { kind: "none" };
    });
  }, []);

  const closeWith = useCallback(
    (value: boolean | string | null) => {
      setState((prev) => {
        if (prev.kind === "confirm" && typeof value === "boolean") prev.resolve(value);
        if (prev.kind === "prompt") prev.resolve(value as string | null);
        return { kind: "none" };
      });
    },
    [],
  );

  return (
    <Ctx.Provider value={{ confirm, prompt }}>
      {children}
      {state.kind !== "none" &&
        createPortal(
          <DialogShell
            state={state}
            inputRef={inputRef}
            inputValue={inputValue}
            setInputValue={setInputValue}
            onConfirm={() => {
              if (state.kind === "confirm") closeWith(true);
              if (state.kind === "prompt") closeWith(inputValue);
            }}
            onCancel={cancelActiveDialog}
          />,
          document.body,
        )}
    </Ctx.Provider>
  );
}

function DialogShell({
  state,
  inputRef,
  inputValue,
  setInputValue,
  onConfirm,
  onCancel,
}: {
  state: Exclude<DialogState, { kind: "none" }>;
  inputRef: React.RefObject<HTMLInputElement>;
  inputValue: string;
  setInputValue: (s: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const opts = state.opts;
  const isPrompt = state.kind === "prompt";
  const severity: Severity = isPrompt ? "default" : ((opts as ConfirmOptions).severity ?? "default");
  const defaultTitle =
    state.kind === "prompt" ? "Enter a value" : severity === "danger" ? "Confirm delete" : "Confirm";
  const defaultConfirmLabel =
    isPrompt ? "OK" : severity === "danger" ? "Delete" : severity === "warning" ? "Continue" : "OK";
  const title = opts.title ?? defaultTitle;

  const confirmClass =
    severity === "danger"
      ? "btn-danger"
      : severity === "warning"
        ? "btn border-warning/50 bg-warning/10 text-warning hover:bg-warning/20"
        : "btn-primary";
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  return (
    <Modal
      open
      onClose={onCancel}
      title={title}
      initialFocusRef={isPrompt ? inputRef : confirmButtonRef}
      size="sm"
      className="card max-w-md w-full p-4 space-y-3 shadow-lg"
    >
      <h2 className="text-base font-semibold text-text">
        {title}
      </h2>
      <p className="text-sm text-text whitespace-pre-wrap">{opts.message}</p>
      {state.kind === "prompt" && (
        <input
          ref={inputRef}
          className="input"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onConfirm();
            }
          }}
          placeholder={(state.opts as PromptOptions).placeholder}
        />
      )}
      <div className="flex justify-end gap-2 pt-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>
          {opts.cancelLabel ?? "Cancel"}
        </button>
        <button
          ref={confirmButtonRef}
          type="button"
          className={confirmClass}
          onClick={onConfirm}
          autoFocus={!isPrompt}
        >
          {opts.confirmLabel ?? defaultConfirmLabel}
        </button>
      </div>
    </Modal>
  );
}

export function useConfirm(): (opts: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useConfirm must be used inside <ConfirmDialogProvider>");
  }
  return ctx.confirm;
}

export function usePrompt(): (opts: PromptOptions) => Promise<string | null> {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("usePrompt must be used inside <ConfirmDialogProvider>");
  }
  return ctx.prompt;
}

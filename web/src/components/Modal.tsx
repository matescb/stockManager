import {
  useEffect,
  useId,
  useRef,
  type MouseEvent,
  type ReactNode,
  type RefObject,
} from "react";

type ModalSize = "sm" | "md" | "lg";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement>;
  size?: ModalSize;
  className?: string;
};

const SIZE_CLASSES: Record<ModalSize, string> = {
  sm: "max-w-lg",
  md: "max-w-5xl",
  lg: "max-w-6xl",
};

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(","),
  )).filter(element => element.getAttribute("aria-hidden") !== "true");
}

export function Modal({
  open,
  onClose,
  title,
  children,
  initialFocusRef,
  size = "md",
  className,
}: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;

    const focusTimer = window.setTimeout(() => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusTarget = initialFocusRef?.current ?? focusableElements(dialog)[0] ?? dialog;
      focusTarget.focus();
    }, 0);

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = focusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKeyDown);
      const restoreFocus = restoreFocusRef.current;
      if (restoreFocus && document.contains(restoreFocus)) {
        restoreFocus.focus();
      }
      restoreFocusRef.current = null;
    };
  }, [open, onClose, initialFocusRef]);

  if (!open) return null;

  function onBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) onClose();
  }

  const panelClassName = `max-h-[90vh] overflow-y-auto ${
    className ?? `card w-full ${SIZE_CLASSES[size]}`
  }`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      ref={dialogRef}
      onMouseDown={onBackdropMouseDown}
    >
      <h2 id={titleId} className="sr-only">
        {title}
      </h2>
      <div className={panelClassName}>
        {children}
      </div>
    </div>
  );
}

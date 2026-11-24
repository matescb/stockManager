/** Prominent "scan this next" banner for the one pending bag. */
import { useEffect, useState } from "react";
import type React from "react";
import { ScanLine, X } from "lucide-react";
import { followUpPlaceholder, followUpPrompt, type FollowUp } from "./followUp";

type Props = {
  followUp: FollowUp;
  /** Typed answer — same handling as a scanned one. */
  onAnswer: (text: string) => void;
  onCancel: () => void;
};

export default function FollowUpPrompt({ followUp, onAnswer, onCancel }: Props) {
  const [text, setText] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    onAnswer(text.trim());
    setText("");
  }

  return (
    <div role="alert" data-testid="follow-up-prompt" className="card p-3 border-2 border-warning bg-warning/10 space-y-2">
      <div className="flex items-start gap-2">
        <ScanLine size={20} className="text-warning shrink-0 mt-0.5" />
        <div className="font-medium flex-1">{followUpPrompt(followUp)}</div>
        <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={onCancel} title="Esc">
          <X size={14} /> Cancel
        </button>
      </div>
      <form className="flex gap-2" onSubmit={submit}>
        <label className="sr-only" htmlFor="follow-up-answer">{followUpPlaceholder(followUp.missing)}</label>
        <input
          id="follow-up-answer"
          className="input min-w-0 flex-1"
          inputMode={followUp.missing === "quantity" ? "numeric" : undefined}
          placeholder={followUpPlaceholder(followUp.missing)}
          value={text}
          onChange={e => setText(e.target.value)}
        />
        <button type="submit" className="btn-primary btn-sm" disabled={!text.trim()}>Use</button>
      </form>
    </div>
  );
}

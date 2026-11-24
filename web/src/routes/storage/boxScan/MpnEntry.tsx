/** Inline "type the MPN" form for a scanned bag label that carried none. */
import { useState } from "react";
import type React from "react";
import { Search } from "lucide-react";

type Props = { id: string; disabled?: boolean; onSubmit: (mpn: string) => void };

export default function MpnEntry({ id, disabled, onSubmit }: Props) {
  const [mpn, setMpn] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = mpn.trim();
    if (!v) return;
    onSubmit(v);
    setMpn("");
  }

  return (
    <form className="flex gap-2 mt-1" onSubmit={submit}>
      <label className="sr-only" htmlFor={`mpn-entry-${id}`}>Manufacturer part number</label>
      <input
        id={`mpn-entry-${id}`}
        className="input min-w-0 flex-1"
        placeholder="Mfr. part number"
        value={mpn}
        disabled={disabled}
        onChange={e => setMpn(e.target.value)}
      />
      <button type="submit" className="btn btn-sm inline-flex items-center gap-1" disabled={disabled || !mpn.trim()}>
        <Search size={14} /> Look up
      </button>
    </form>
  );
}

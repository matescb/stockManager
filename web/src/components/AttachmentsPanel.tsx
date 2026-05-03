import { useRef, useState, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2, Download, UploadCloud, Paperclip } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { formatDateTime } from "@/lib/format";

type Attachment = {
  id: string;
  object_type: string;
  object_id: string;
  file_name: string;
  file_type: string;
  mime_type: string | null;
  size_bytes: number;
  created_at: string;
};

type FileType = "other" | "datasheet" | "invoice" | "image" | "cad" | "bom";

type Props = {
  objectType: "part" | "order" | "build";
  objectId: string;
  canWrite: boolean;
};

/**
 * 10 MiB hard cap — matches `MAX_UPLOAD_BYTES` in
 * `backend/app/core/config.py`. The backend rejects anything larger with
 * a 413, so the FE guard exists purely to give a fast, in-browser error
 * instead of waiting for the round-trip. If the backend cap is ever
 * raised, bump this in lockstep (or, better, fetch it from a config
 * endpoint).
 */
export const MAX_BYTES = 10 * 1024 * 1024;

/**
 * Allowed MIME types and file extensions per file_type dropdown value.
 * Each entry is checked against both `file.type` (MIME) and the lowercased
 * filename suffix because browsers on network shares may not populate
 * `file.type` correctly.
 *
 * The backend's allow-list (`backend/app/api/routes/attachments.py`)
 * accepts only PNG, JPEG, WebP, and PDF — and validates with magic-byte
 * sniffing, so the declared MIME/extension is never trusted on its own.
 * Server-side validation remains the source of truth; this FE guard just
 * matches that allow-list so users get an immediate error for files the
 * server is going to reject anyway.
 *
 * The `cad` and `bom` buckets are restricted to PDF for now — native CAD
 * formats (.step/.stl/.dxf) and tabular BOMs (.csv/.xlsx) need a backend
 * expansion before the FE can advertise them. Until then they fall under
 * the `other` bucket if a user really has one to attach.
 *
 * `other` is intentionally permissive — any extension is accepted
 * (still subject to MAX_BYTES, and the server still enforces its own
 * MIME allow-list).
 */
export const ALLOWED_MIME_FOR_TYPE: Record<
  FileType,
  { mimes: string[]; exts: string[] }
> = {
  datasheet: {
    mimes: ["application/pdf"],
    exts: [".pdf"],
  },
  invoice: {
    mimes: ["application/pdf", "image/jpeg", "image/png", "image/webp"],
    exts: [".pdf", ".jpg", ".jpeg", ".png", ".webp"],
  },
  image: {
    mimes: ["image/jpeg", "image/png", "image/webp"],
    exts: [".jpg", ".jpeg", ".png", ".webp"],
  },
  cad: {
    mimes: ["application/pdf"],
    exts: [".pdf"],
  },
  bom: {
    mimes: ["application/pdf"],
    exts: [".pdf"],
  },
  other: {
    mimes: [],
    exts: [],
  },
};

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * Validates a file against the size cap and the type-specific allow-list.
 * Returns null on pass, or an error string on failure.
 */
export function validateFile(file: File, fileType: FileType): string | null {
  if (file.size > MAX_BYTES) {
    return `File is too large (${humanSize(file.size)}). Maximum allowed size is ${humanSize(MAX_BYTES)}.`;
  }

  const rule = ALLOWED_MIME_FOR_TYPE[fileType];
  // `other` has empty lists — all files are allowed for that type
  if (rule.exts.length === 0 && rule.mimes.length === 0) return null;

  const nameLower = file.name.toLowerCase();
  const extOk = rule.exts.some(ext => nameLower.endsWith(ext));
  const mimeOk = file.type !== "" && rule.mimes.includes(file.type);

  if (!extOk && !mimeOk) {
    return `"${file.name}" is not allowed for type "${fileType}". Accepted: ${rule.exts.join(", ")}.`;
  }

  return null;
}

export default function AttachmentsPanel({ objectType, objectId, canWrite }: Props) {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const queryKey = useWsKey("attachments", objectType, objectId);
  const attachmentsQuery = useQuery({
    queryKey,
    queryFn: () =>
      api.get<Attachment[]>(`/attachments/by-object/${objectType}/${objectId}`),
  });
  const { data, isLoading, isError } = attachmentsQuery;

  const [file, setFile] = useState<File | null>(null);
  const [fileType, setFileType] = useState<FileType>("other");
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  /** The `accept` string wired to the file input — pre-filters the OS picker. */
  const acceptAttr = useMemo(() => {
    const rule = ALLOWED_MIME_FOR_TYPE[fileType];
    if (rule.exts.length === 0 && rule.mimes.length === 0) return undefined;
    return [...rule.mimes, ...rule.exts].join(",");
  }, [fileType]);

  /** Human-readable summary of allowed types shown in the helper text. */
  const allowedLabel = useMemo(() => {
    const rule = ALLOWED_MIME_FOR_TYPE[fileType];
    if (rule.exts.length === 0) return `Any file type, max ${humanSize(MAX_BYTES)}`;
    return `Accepted: ${rule.exts.join(", ")} · max ${humanSize(MAX_BYTES)}`;
  }, [fileType]);

  async function doUpload() {
    if (!file) {
      toast.error("Pick a file first.");
      return;
    }
    const err = validateFile(file, fileType);
    if (err) {
      toast.error(err);
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("object_type", objectType);
      form.append("object_id", objectId);
      form.append("file_type", fileType || "other");
      form.append("file", file);
      await api.upload<Attachment>("/attachments", form);
      toast.success("Attachment uploaded.");
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      qc.invalidateQueries({ queryKey });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function doDelete(a: Attachment) {
    if (!(await confirm({ message: `Delete ${a.file_name}?`, severity: "danger" }))) return;
    try {
      await api.delete(`/attachments/${a.id}`);
      toast.success("Attachment deleted.");
      qc.invalidateQueries({ queryKey });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Delete failed");
    }
  }

  function onDrop(ev: React.DragEvent) {
    ev.preventDefault();
    setDragOver(false);
    const f = ev.dataTransfer.files?.[0];
    if (!f) return;
    const err = validateFile(f, fileType);
    if (err) {
      toast.error(err);
      return;
    }
    setFile(f);
  }

  return (
    <div className="card p-4">
      <div className="flex items-center mb-3">
        <Paperclip className="w-4 h-4 mr-2 text-muted" />
        <h3 className="text-md font-semibold">Attachments</h3>
      </div>

      {canWrite && (
        <div
          className={`mb-3 rounded-md border-2 border-dashed p-3 transition-colors ${
            dragOver ? "border-accent bg-accent/10" : "border-border"
          }`}
          onDragOver={ev => {
            ev.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[180px]">
              <label className="label" htmlFor="attachment-file">File</label>
              <input
                id="attachment-file"
                ref={inputRef}
                type="file"
                className="input"
                accept={acceptAttr}
                onChange={ev => {
                  const f = ev.target.files?.[0] ?? null;
                  if (f) {
                    const err = validateFile(f, fileType);
                    if (err) {
                      toast.error(err);
                      ev.target.value = "";
                      setFile(null);
                      return;
                    }
                  }
                  setFile(f);
                }}
              />
            </div>
            <div className="w-32">
              <label className="label" htmlFor="attachment-type">Type</label>
              <select
                id="attachment-type"
                className="input"
                value={fileType}
                onChange={ev => setFileType(ev.target.value as FileType)}
              >
                <option value="other">other</option>
                <option value="datasheet">datasheet</option>
                <option value="invoice">invoice</option>
                <option value="image">image</option>
                <option value="cad">cad</option>
                <option value="bom">bom</option>
              </select>
            </div>
            <button className="btn-primary" onClick={doUpload} disabled={busy || !file}>
              <UploadCloud className="inline w-4 h-4 mr-1" />
              {busy ? "Uploading…" : "Upload"}
            </button>
          </div>
          <div className="text-xs text-muted mt-2">
            Drag & drop a file onto this box, or pick one above. {allowedLabel}.
          </div>
        </div>
      )}

      {isError ? (
        <InlineQueryError query={attachmentsQuery} label="attachments" />
      ) : isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : !data || data.length === 0 ? (
        <div className="text-muted text-sm">No attachments yet.</div>
      ) : (
        <ul className="divide-y divide-border">
          {data.map(a => (
            <li key={a.id} className="py-2 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-sm">{a.file_name}</span>
                  <span className="pill text-[10px] uppercase">{a.file_type}</span>
                </div>
                <div className="text-xs text-muted">
                  {humanSize(a.size_bytes)} · {formatDateTime(a.created_at)}
                </div>
              </div>
              <a
                className="btn text-xs"
                href={`/api/attachments/${a.id}/download`}
                target="_blank"
                rel="noreferrer"
              >
                <Download className="inline w-3 h-3 mr-1" />
                Download
              </a>
              {canWrite && (
                <button className="btn-danger text-xs" onClick={() => doDelete(a)}>
                  <Trash2 className="inline w-3 h-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

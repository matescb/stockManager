import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2, Download, UploadCloud, Paperclip } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";

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

type Props = {
  objectType: "part" | "order" | "build";
  objectId: string;
  canWrite: boolean;
};

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function AttachmentsPanel({ objectType, objectId, canWrite }: Props) {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const queryKey = ["attachments", objectType, objectId];
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      api.get<Attachment[]>(`/attachments/by-object/${objectType}/${objectId}`),
  });

  const [file, setFile] = useState<File | null>(null);
  const [fileType, setFileType] = useState<string>("other");
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function doUpload() {
    if (!file) {
      toast.error("Pick a file first.");
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
      toast.error(e instanceof ApiError ? e.message : "Upload failed");
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
      toast.error(e instanceof ApiError ? e.message : "Delete failed");
    }
  }

  function onDrop(ev: React.DragEvent) {
    ev.preventDefault();
    setDragOver(false);
    const f = ev.dataTransfer.files?.[0];
    if (f) setFile(f);
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
              <label className="label">File</label>
              <input
                ref={inputRef}
                type="file"
                className="input"
                onChange={ev => setFile(ev.target.files?.[0] ?? null)}
              />
            </div>
            <div className="w-32">
              <label className="label">Type</label>
              <select
                className="input"
                value={fileType}
                onChange={ev => setFileType(ev.target.value)}
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
            Drag & drop a file onto this box, or pick one above.
          </div>
        </div>
      )}

      {isLoading ? (
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
                  {humanSize(a.size_bytes)} · {new Date(a.created_at).toLocaleString()}
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

import { useOutletContext } from "react-router-dom";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Project } from "@/types";

export default function ProjectData() {
  const { project } = useOutletContext<{ project: Project }>();
  const qc = useQueryClient();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [notes, setNotes] = useState(project.notes_markdown ?? "");
  async function save() {
    await api.patch(`/projects/${project.id}`, { name, description: description || null, notes_markdown: notes || null });
    qc.invalidateQueries({ queryKey: ["project", project.id] });
  }
  return (
    <div className="card p-4 max-w-2xl space-y-3">
      <div>
        <label className="label">Name</label>
        <input className="input" value={name} onChange={e => setName(e.target.value)} />
      </div>
      <div>
        <label className="label">Description</label>
        <textarea className="input" rows={3} value={description} onChange={e => setDescription(e.target.value)} />
      </div>
      <div>
        <label className="label">Notes (Markdown)</label>
        <textarea className="input font-mono" rows={6} value={notes} onChange={e => setNotes(e.target.value)} />
      </div>
      <button className="btn-primary" onClick={save}>Save</button>
    </div>
  );
}

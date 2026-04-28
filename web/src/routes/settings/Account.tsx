import { useAuth } from "@/lib/auth";

export default function Account() {
  const { me } = useAuth();
  if (!me) return null;
  return (
    <div className="max-w-xl">
      <h1 className="text-xl font-semibold mb-4">Account</h1>
      <div className="card p-4 space-y-3 text-sm">
        <div><span className="text-muted">Name:</span> {me.user.name}</div>
        <div><span className="text-muted">Email:</span> {me.user.email}</div>
        <div><span className="text-muted">User ID:</span> <span className="font-mono">{me.user.id}</span></div>
      </div>
    </div>
  );
}

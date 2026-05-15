import { Link } from "react-router-dom";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import AuthShell from "./AuthShell";

export default function RequestReset() {
  const [email, setEmail] = useState("");
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const requestMutation = useApiMutation<unknown, { email: string }>({
    mutationKey: ["auth", "request-password-reset"],
    mutationFn: (payload) => api.post("/auth/request-password-reset", payload),
    onSuccess: () => {
      setSubmittedEmail(email);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Password reset request failed");
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    requestMutation.mutate({ email });
  }

  if (submittedEmail) {
    return (
      <AuthShell title="Check your inbox">
        <div className="space-y-4">
          <p className="text-sm">
            If an account exists for <strong>{submittedEmail}</strong>, a password reset link will arrive shortly.
          </p>
          <p className="text-sm text-muted">
            <Link to="/login" className="text-accent hover:underline">Back to sign in</Link>
          </p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Reset password">
      <form onSubmit={submit} className="space-y-4">
        {err && (
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {err}
          </div>
        )}
        <div>
          <label className="label" htmlFor="reset-email">Email</label>
          <input
            id="reset-email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={e => setEmail(e.target.value)}
          />
        </div>
        <button className="btn-primary w-full" disabled={requestMutation.isPending}>
          {requestMutation.isPending ? "Sending…" : "Send reset link"}
        </button>
        <p className="text-sm text-muted">
          Remembered it? <Link to="/login" className="text-accent hover:underline">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}

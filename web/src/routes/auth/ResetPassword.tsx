import { Link, useSearchParams } from "react-router-dom";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { getPasswordStrengthError } from "@/lib/passwordStrength";
import AuthShell from "./AuthShell";

function resetErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) {
    return "Password reset failed";
  }
  if (e.code === "auth.reset_expired") {
    return "This reset link has expired.";
  }
  if (e.code === "auth.reset_used") {
    return "This reset link has already been used.";
  }
  if (e.code === "auth.reset_invalid") {
    return "This reset link is invalid.";
  }
  if (e.code === "auth.weak_password") {
    return e.message;
  }
  return e.userMessage;
}

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(token ? null : "This reset link is missing a token.");
  const [complete, setComplete] = useState(false);

  const resetMutation = useApiMutation<unknown, { token: string; new_password: string }>({
    mutationKey: ["auth", "reset-password"],
    mutationFn: (payload) => api.post("/auth/reset-password", payload),
    onSuccess: () => {
      setComplete(true);
    },
    onError: (e) => {
      setErr(resetErrorMessage(e));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const passwordError = getPasswordStrengthError(password);
    if (passwordError) {
      setErr(passwordError);
      return;
    }
    setErr(null);
    resetMutation.mutate({ token, new_password: password });
  }

  if (complete) {
    return (
      <AuthShell title="Password reset">
        <div className="space-y-4">
          <p className="text-sm">Your password has been updated.</p>
          <p className="text-sm text-muted">
            <Link to="/login" className="text-accent hover:underline">Sign in</Link>
          </p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Set new password">
      <form onSubmit={submit} className="space-y-4">
        {err && (
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {err}
          </div>
        )}
        <div>
          <label className="label" htmlFor="new-password">New password</label>
          <input
            id="new-password"
            className="input"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={e => setPassword(e.target.value)}
            disabled={!token}
          />
        </div>
        <button className="btn-primary w-full" disabled={!token || resetMutation.isPending}>
          {resetMutation.isPending ? "Saving…" : "Save new password"}
        </button>
        <p className="text-sm text-muted">
          Need a fresh link? <Link to="/auth/request-reset" className="text-accent hover:underline">Request one</Link>
        </p>
      </form>
    </AuthShell>
  );
}

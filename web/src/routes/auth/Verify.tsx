/**
 * Email-verification landing page (SEC2-014).
 *
 * The user arrives here by clicking the link in their verification email:
 *   /verify?id=<pending_id>&token=<plaintext_token>
 *
 * We POST to /auth/verify with the id + token.  On success the server
 * issues a session cookie and we refresh the auth context, then navigate
 * to the app.  On failure we show an error with a link back to signup.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthShell from "./AuthShell";

type State = "verifying" | "success" | "error";

export default function Verify() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const { refresh } = useAuth();
  const [state, setState] = useState<State>("verifying");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const id = params.get("id");
  const token = params.get("token");

  useEffect(() => {
    if (!id || !token) {
      setState("error");
      setErrMsg("Invalid verification link — the id or token is missing.");
      return;
    }

    let cancelled = false;

    api
      .post("/auth/verify", { id, token })
      .then(async () => {
        if (cancelled) return;
        await refresh();
        setState("success");
        nav("/parts", { replace: true });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState("error");
        setErrMsg(
          e instanceof ApiError
            ? e.userMessage
            : "Verification failed — please try again.",
        );
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, token]);

  if (state === "verifying") {
    return (
      <AuthShell title="Verifying your email…">
        <p className="text-sm text-muted">Please wait…</p>
      </AuthShell>
    );
  }

  if (state === "error") {
    return (
      <AuthShell title="Verification failed">
        <div className="space-y-4">
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {errMsg}
          </div>
          <p className="text-sm text-muted">
            The link may have expired (links are valid for 24 hours).{" "}
            <Link to="/signup" className="text-accent hover:underline">Sign up again</Link>
            {" "}to receive a fresh link.
          </p>
        </div>
      </AuthShell>
    );
  }

  // success — navigation already triggered in useEffect
  return (
    <AuthShell title="Email verified!">
      <p className="text-sm">Redirecting to your workspace…</p>
    </AuthShell>
  );
}

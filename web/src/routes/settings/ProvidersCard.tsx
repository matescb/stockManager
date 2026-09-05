import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { PROVIDERS, providerLabel, providerNeedsSecret } from "@/lib/providers";
import { wsKeyOf } from "@/lib/queryKeys";

export type ProviderCredential = {
  provider: string;
  has_api_key: boolean;
  has_api_secret: boolean;
};

export type ProvidersWorkspace = {
  parts_provider: string;
  provider_credentials: ProviderCredential[];
};

type CredentialsBody = {
  provider: string;
  api_key?: string;
  api_secret?: string;
};

type CredentialsResponse = {
  provider: string;
  has_api_key: boolean;
  has_api_secret: boolean;
  provider_credentials: ProviderCredential[];
};

interface ProvidersCardProps {
  workspace: ProvidersWorkspace;
  workspaceId: string | null | undefined;
}

/**
 * Additional (secondary) parts providers.
 *
 * The workspace's PRIMARY provider is configured on the card above and
 * owns the part's own manufacturer / MPN / description. Anything set
 * here is a second catalog source: it contributes price and stock data
 * under its own namespace and never rewrites a part's fields. Only
 * providers that aren't already the primary are listed.
 */
export function ProvidersCard({ workspace, workspaceId }: ProvidersCardProps) {
  const secondaries = PROVIDERS.filter(p => p.name !== workspace.parts_provider);

  if (secondaries.length === 0) return null;

  return (
    <div className="card p-4 mb-4 space-y-4 text-sm">
      <div>
        <h2 className="card-title">Additional providers</h2>
        <div className="text-xs text-muted">
          A second catalog source for price and availability. Its data lands
          on a part&apos;s Sourcing tab under that provider&apos;s own name and
          never overwrites the part&apos;s manufacturer or description.
        </div>
      </div>
      {secondaries.map(provider => (
        <ProviderRow
          key={provider.name}
          provider={provider.name}
          credential={workspace.provider_credentials.find(
            c => c.provider === provider.name,
          )}
          workspaceId={workspaceId}
        />
      ))}
    </div>
  );
}

interface ProviderRowProps {
  provider: string;
  credential: ProviderCredential | undefined;
  workspaceId: string | null | undefined;
}

function ProviderRow({ provider, credential, workspaceId }: ProviderRowProps) {
  const qc = useQueryClient();
  const label = providerLabel(provider);
  const needsSecret = providerNeedsSecret(provider);

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [secretTouched, setSecretTouched] = useState(false);
  const [hasKey, setHasKey] = useState(Boolean(credential?.has_api_key));
  const [hasSecret, setHasSecret] = useState(Boolean(credential?.has_api_secret));

  // Resync when the workspace query refetches — the same pattern as
  // SourcingCard, so a save elsewhere doesn't leave stale pills here.
  useEffect(() => {
    setApiKey("");
    setApiSecret("");
    setKeyTouched(false);
    setSecretTouched(false);
    setHasKey(Boolean(credential?.has_api_key));
    setHasSecret(Boolean(credential?.has_api_secret));
  }, [credential]);

  const saveMutation = useMutation({
    mutationKey: ["workspace", "provider-credentials", provider],
    mutationFn: (body: CredentialsBody) =>
      api.put<CredentialsResponse, CredentialsBody>(
        "/workspaces/current/provider-credentials",
        body,
      ),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      setApiKey("");
      setApiSecret("");
      setKeyTouched(false);
      setSecretTouched(false);
      setHasKey(saved.has_api_key);
      setHasSecret(saved.has_api_secret);
      toast.success(
        saved.has_api_key || saved.has_api_secret
          ? `${label} credentials saved.`
          : `${label} credentials cleared.`,
      );
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const configured = hasKey;
  const busy = saveMutation.isPending;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const body: CredentialsBody = { provider };
    if (keyTouched) body.api_key = apiKey;
    if (secretTouched) body.api_secret = apiSecret;
    // The server rejects a body with neither field; don't send one.
    if (!keyTouched && !secretTouched) return;
    saveMutation.mutate(body);
  }

  function clearAll() {
    saveMutation.mutate({ provider, api_key: "", api_secret: "" });
  }

  return (
    <form onSubmit={submit} className="space-y-2 border-t border-border pt-3 first:border-0 first:pt-0">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-medium">{label}</h3>
        {configured && (
          <span
            className="pill bg-success/10 text-success"
            aria-label={`${label} credentials configured`}
          >
            Configured ✓
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <input
          className="input flex-1 min-w-[12rem] font-mono text-xs"
          type="password"
          autoComplete="off"
          aria-label={needsSecret ? `${label} Client ID` : `${label} API key`}
          value={apiKey}
          onChange={(e) => {
            setKeyTouched(true);
            setApiKey(e.target.value);
          }}
          placeholder={
            hasKey
              ? `•••••••• (${needsSecret ? "Client ID" : "API key"} set)`
              : needsSecret
                ? "Client ID"
                : "API key"
          }
        />
        {needsSecret && (
          <input
            className="input flex-1 min-w-[12rem] font-mono text-xs"
            type="password"
            autoComplete="off"
            aria-label={`${label} Client Secret`}
            value={apiSecret}
            onChange={(e) => {
              setSecretTouched(true);
              setApiSecret(e.target.value);
            }}
            placeholder={hasSecret ? "•••••••• (Secret set)" : "Client Secret"}
          />
        )}
        <button
          className="btn-primary"
          type="submit"
          disabled={busy || (!keyTouched && !secretTouched)}
        >
          Save
        </button>
        {(hasKey || hasSecret) && (
          <button className="btn" type="button" disabled={busy} onClick={clearAll}>
            Clear
          </button>
        )}
      </div>
    </form>
  );
}

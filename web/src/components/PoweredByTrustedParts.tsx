import { isSafeHttpUrl } from "@/lib/url";

const DEFAULT_TRUSTEDPARTS_URL = "https://www.trustedparts.com";

type Props = {
  primaryUrl?: string;
  size?: "sm" | "md";
  className?: string;
};

export function PoweredByTrustedParts({
  primaryUrl,
  size = "sm",
  className,
}: Props) {
  const classes = [
    "pill",
    "bg-accent/15",
    "text-accent",
    size === "md" ? "text-sm" : "",
    className ?? "",
  ].filter(Boolean).join(" ");
  const href = primaryUrl && isSafeHttpUrl(primaryUrl)
    ? primaryUrl
    : DEFAULT_TRUSTEDPARTS_URL;

  return (
    <a
      className={classes}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      Powered by TrustedParts
    </a>
  );
}

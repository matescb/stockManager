type Props = {
  source: "trustedparts";
  className?: string;
};

function unsupportedSource(source: never): never {
  throw new Error(`Unsupported sourcing source: ${String(source)}`);
}

export function SourcingSourceLabel({ source, className }: Props) {
  switch (source) {
    case "trustedparts": {
      const classes = [
        "pill",
        "bg-accent/15",
        "text-accent",
        className ?? "",
      ].filter(Boolean).join(" ");

      return (
        <span className={classes} aria-label="Source: TrustedParts">
          TrustedParts
        </span>
      );
    }
    default:
      return unsupportedSource(source);
  }
}

import { Toaster } from "sonner";
import { useTheme } from "@/lib/theme";

export default function ThemedToaster() {
  const { resolved } = useTheme();
  return (
    <Toaster
      theme={resolved}
      richColors
      closeButton
      position="bottom-right"
      toastOptions={{
        // Match the app's small-text + rounded primitives.
        className: "text-sm",
      }}
    />
  );
}

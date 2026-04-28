import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme, ThemePreference } from "@/lib/theme";

const NEXT: Record<ThemePreference, ThemePreference> = {
  system: "light",
  light: "dark",
  dark: "system",
};

const ICON: Record<ThemePreference, typeof Sun> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

const TITLE: Record<ThemePreference, string> = {
  system: "Theme: system (click to set light)",
  light: "Theme: light (click to set dark)",
  dark: "Theme: dark (click to follow system)",
};

export default function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  const Icon = ICON[preference];
  return (
    <button
      type="button"
      className="btn-ghost btn-sm"
      title={TITLE[preference]}
      aria-label={TITLE[preference]}
      onClick={() => setPreference(NEXT[preference])}
    >
      <Icon size={16} />
    </button>
  );
}

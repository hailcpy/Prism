"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Moon, Sun, Monitor } from "lucide-react";

export function ThemeToggle() {
  const [mounted, setMounted] = useState(false);
  const { theme, setTheme } = useTheme();

  // useEffect only runs on the client, so now we can safely show the UI
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <div className="w-[104px] h-[36px]" />;
  }

  return (
    <div className="flex items-center p-1 border border-black/10 dark:border-white/10 rounded-lg bg-black/5 dark:bg-white/5 backdrop-blur-md">
      <button
        onClick={() => setTheme("light")}
        className={`p-1.5 rounded-md flex items-center justify-center transition-all ${
          theme === "light"
            ? "bg-white text-black shadow-sm dark:bg-zinc-800 dark:text-white"
            : "text-zinc-500 hover:text-black dark:text-zinc-400 dark:hover:text-white"
        }`}
        aria-label="Light theme"
      >
        <Sun className="w-4 h-4" />
      </button>
      <button
        onClick={() => setTheme("system")}
        className={`p-1.5 rounded-md flex items-center justify-center transition-all ${
          theme === "system" || theme === "auto"
            ? "bg-white text-black shadow-sm dark:bg-zinc-800 dark:text-white"
            : "text-zinc-500 hover:text-black dark:text-zinc-400 dark:hover:text-white"
        }`}
        aria-label="System theme"
      >
        <Monitor className="w-4 h-4" />
      </button>
      <button
        onClick={() => setTheme("dark")}
        className={`p-1.5 rounded-md flex items-center justify-center transition-all ${
          theme === "dark"
            ? "bg-white text-black shadow-sm dark:bg-zinc-800 dark:text-white"
            : "text-zinc-500 hover:text-black dark:text-zinc-400 dark:hover:text-white"
        }`}
        aria-label="Dark theme"
      >
        <Moon className="w-4 h-4" />
      </button>
    </div>
  );
}

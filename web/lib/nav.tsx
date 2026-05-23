"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "./theme-toggle";

const links = [
  { href: "/", label: "Chat" },
  { href: "/metrics", label: "Metrics" },
  // { href: "/dashboards", label: "Dashboards" },
  { href: "/settings", label: "Settings" },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <nav className="sticky top-0 z-50 flex items-center h-14 px-6 border-b border-black/10 dark:border-white/10 bg-white/70 dark:bg-zinc-900/70 backdrop-blur-xl">
      <div className="flex items-center gap-2 mr-8">
        <div className="w-5 h-5 rounded-[4px] bg-gradient-to-br from-[#ff6d4d] via-[#009f8f] to-[#2453ff] shadow-[0_2px_8px_rgba(0,159,143,0.3)]" />
        <span className="font-bold text-base text-zinc-900 dark:text-zinc-100">
          Prism
        </span>
      </div>
      <div className="flex items-center gap-1">
        {links.map((link) => {
          const active =
            link.href === "/"
              ? pathname === "/"
              : pathname?.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                active
                  ? "bg-black/5 dark:bg-white/10 text-black dark:text-white"
                  : "text-zinc-500 hover:text-black dark:text-zinc-400 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </div>
      <div className="ml-auto">
        <ThemeToggle />
      </div>
    </nav>
  );
}

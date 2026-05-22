"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Chat" },
  { href: "/metrics", label: "Metrics" },
  { href: "/dashboards", label: "Dashboards" },
  { href: "/settings", label: "Settings" },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <nav className="top-nav">
      <div className="nav-brand">
        <div className="nav-brand-mark" />
        <span>Prism</span>
      </div>
      {links.map((link) => {
        const active =
          link.href === "/"
            ? pathname === "/"
            : pathname?.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={active ? "active" : undefined}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}

import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/lib/nav";

export const metadata: Metadata = {
  title: "Prism — LLM Chatbot",
  description: "Multi-provider LLM chatbot with inference logging",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="root-shell">
          <NavBar />
          {children}
        </div>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}

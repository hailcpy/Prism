import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Olive — LLM Chatbot",
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

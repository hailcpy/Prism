import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/lib/nav";
import { ThemeProvider } from "@/lib/theme-provider";

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
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <div className="root-shell">
            <NavBar />
            {children}
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}

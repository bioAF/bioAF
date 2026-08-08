import type { Metadata } from "next";
import "./globals.css";
import { FloatingAssistant } from "@/components/assistant/FloatingAssistant";
import { ThemeProvider } from "@/components/theme/ThemeProvider";
import { ConfirmProvider } from "@/hooks/useConfirm";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "bioAF",
  description: "Computational Biology Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Resolve and apply the saved theme before first paint so there is no flash
            of the wrong theme. Kept in sync with src/lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="bg-canvas text-gray-900">
        <ThemeProvider>
          {/* ConfirmProvider sits in the ROOT layout, not the (app) shell, because
              the setup wizard at /setup is outside that group and guards the
              bootstrap steps with confirmations too. */}
          <ConfirmProvider>
            {children}
            {/* Mounted once here so the assistant follows the user across pages without losing the
                conversation (the root layout persists across client navigation). */}
            <FloatingAssistant />
          </ConfirmProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

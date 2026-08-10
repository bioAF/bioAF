import type { Metadata } from "next";
import "./globals.css";
import { FloatingAssistant } from "@/components/assistant/FloatingAssistant";
import { DocumentTitle } from "@/components/layout/DocumentTitle";
import { ThemeProvider } from "@/components/theme/ThemeProvider";
import { ConfirmProvider } from "@/hooks/useConfirm";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
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
        {/* A plain element, NOT `metadata.title`. Next re-asserts a metadata title
            asynchronously after hydration, which silently reverted the per-page
            title DocumentTitle had already set (measured: the effect set "Login -
            bioAF", and a second later the tab read "bioAF" again). React renders
            this once and never touches it, so it is the pre-hydration title and
            DocumentTitle owns it from then on. */}
        <title>bioAF</title>
        {/* Resolve and apply the saved theme before first paint so there is no flash
            of the wrong theme. Kept in sync with src/lib/theme.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="bg-canvas text-gray-900">
        <ThemeProvider>
          {/* Here rather than in the (app) shell for two reasons: it names the tab
              on /login and /setup too, and the shell returns a boot splash before
              it renders its children, which would leave the tab unnamed for
              exactly as long as the app was still starting. */}
          <DocumentTitle />
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

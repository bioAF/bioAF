import type { Metadata } from "next";
import "./globals.css";
import { FloatingAssistant } from "@/components/assistant/FloatingAssistant";

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
    <html lang="en">
      <body className="bg-gray-50">
        {children}
        {/* Mounted once here so the assistant follows the user across pages without losing the
            conversation (the root layout persists across client navigation). */}
        <FloatingAssistant />
      </body>
    </html>
  );
}

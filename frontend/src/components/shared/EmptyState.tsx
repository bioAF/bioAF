import type { ReactNode } from "react";
import { Card } from "@/components/ui/Card";

/**
 * What a list says when it loaded successfully and holds nothing. Only for a
 * successful empty answer: a failed load is an ErrorState, never this.
 */
export function EmptyState({ title, message, action }: { title: string; message: ReactNode; action?: ReactNode }) {
  return (
    <Card padding="none" className="p-12 text-center">
      <h2 className="mb-2 text-lg font-semibold text-gray-500">{title}</h2>
      <p className={`text-gray-500 ${action ? "mb-4" : ""}`.trim()}>{message}</p>
      {action}
    </Card>
  );
}

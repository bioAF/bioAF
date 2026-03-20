"use client";

import { useEffect, type ReactNode } from "react";

export interface DetailField {
  label: string;
  value: ReactNode;
}

interface DetailModalProps {
  title: string;
  onClose: () => void;
  fields: DetailField[];
  actions?: ReactNode;
}

export function DetailModal({ title, onClose, fields, actions }: DetailModalProps) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
          >
            &times;
          </button>
        </div>
        <dl className="p-4 grid grid-cols-2 gap-x-4 gap-y-3">
          {fields.map((f) => (
            <div key={f.label}>
              <dt className="text-xs font-medium text-gray-500 uppercase">{f.label}</dt>
              <dd className="mt-0.5 text-sm text-gray-900">{f.value || "---"}</dd>
            </div>
          ))}
        </dl>
        {actions && (
          <div className="flex justify-end gap-2 px-4 pb-4">
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}

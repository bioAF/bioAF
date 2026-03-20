"use client";

import { useEffect } from "react";

interface PlotModalProps {
  url: string;
  title: string;
  onClose: () => void;
}

export function PlotModal({ url, title, onClose }: PlotModalProps) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-lg shadow-xl max-w-[90vw] max-h-[90vh] overflow-auto mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
          <h3 className="font-medium text-sm">{title}</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
          >
            &times;
          </button>
        </div>
        <div className="p-4">
          <img src={url} alt={title} className="max-w-full" />
        </div>
      </div>
    </div>
  );
}

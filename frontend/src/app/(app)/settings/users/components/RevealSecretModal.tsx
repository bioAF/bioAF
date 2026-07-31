"use client";

import { useState } from "react";

interface Props {
  title: string;
  secret: string;
  description?: string;
  onClose: () => void;
}

/**
 * Modal that reveals a long-lived secret (API key or webhook HMAC) exactly
 * once at creation time. The user must explicitly acknowledge before close.
 */
export function RevealSecretModal({ title, secret, description, onClose }: Props) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore -- some browsers block without HTTPS */
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg w-full">
        <h2 className="text-lg font-semibold mb-2">{title}</h2>
        <p className="text-sm text-gray-600 mb-4">
          {description ??
            "This is the only time we will show this. Store it in a secret manager before closing this dialog."}
        </p>
        <div className="bg-gray-50 border border-gray-200 rounded px-3 py-2 font-mono text-xs break-all mb-3">
          {secret}
        </div>
        <div className="flex items-center justify-between mb-4">
          <button
            onClick={copy}
            className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <label className="flex items-start gap-2 text-sm text-gray-700 mb-4">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-1"
          />
          <span>I have saved this value. I understand it cannot be retrieved later.</span>
        </label>
        <div className="flex justify-end">
          <button
            disabled={!acknowledged}
            onClick={onClose}
            className="px-4 py-2 text-sm bg-gray-200 text-gray-800 rounded hover:bg-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

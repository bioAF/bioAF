"use client";

import { useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

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
    // Not dismissible: the secret is shown exactly once, so letting Escape or a
    // stray backdrop click close it would lose a value that cannot be retrieved.
    // The acknowledge checkbox is the only way out, which is the point.
    <Modal open title={title} onClose={onClose} dismissible={false}>
      <p className="text-sm text-gray-600 mb-4">
        {description ??
          "This is the only time we will show this. Store it in a secret manager before closing this dialog."}
      </p>
      <div className="bg-gray-50 border border-gray-200 rounded px-3 py-2 font-mono text-xs break-all mb-3">
        {secret}
      </div>
      <div className="flex items-center justify-between mb-4">
        <Button size="sm"
          onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <label className="flex items-start gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
          className="mt-1"
        />
        <span>I have saved this value. I understand it cannot be retrieved later.</span>
      </label>
      <div className="flex justify-end mt-4">
        <button
          disabled={!acknowledged}
          onClick={onClose}
          className="px-4 py-2 text-sm bg-gray-200 text-gray-800 rounded hover:bg-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Close
        </button>
      </div>
    </Modal>
  );
}

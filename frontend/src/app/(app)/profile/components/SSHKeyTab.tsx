"use client";

import { useConfirm } from "@/hooks/useConfirm";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";

export function SSHKeyTab() {
  const toast = useToast();
  const [sshKey, setSSHKey] = useState<{ configured: boolean; public_key: string | null } | null>(null);
  const confirm = useConfirm();
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    loadSSHKey();
  }, []);

  async function loadSSHKey() {
    try {
      const data = await api.get<{ configured: boolean; public_key: string | null }>("/api/auth/me/ssh-key");
      setSSHKey(data);
    } catch (e) {
      // Silence here reads as "you have no SSH key", which would send the user
      // off to generate a second one.
      logError("loading the SSH key", e);
      toast.error(loadFailureMessage("Your SSH key"));
    }
    setLoading(false);
  }

  async function handleGenerate() {
    // Only gate when there is actually a key to lose: the short-circuit here
    // matches the original `configured && !confirm(...)` exactly.
    if (sshKey?.configured) {
      const ok = await confirm({
        title: "Replace your existing SSH key?",
        message: "This cannot be undone.",
        confirmLabel: "Replace",
        variant: "danger",
      });
      if (!ok) return;
    }
    setGenerating(true);
    setMessage("");
    try {
      const data = await api.post<{ public_key: string; message: string }>("/api/auth/me/ssh-key/generate");
      setSSHKey({ configured: true, public_key: data.public_key });
      setMessage(data.message);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to generate key");
    }
    setGenerating(false);
  }

  function handleCopy() {
    if (!sshKey?.public_key) return;
    try {
      navigator.clipboard.writeText(sshKey.public_key).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }).catch(() => fallbackCopy());
    } catch {
      fallbackCopy();
    }
  }

  function fallbackCopy() {
    if (!sshKey?.public_key) return;
    const textarea = document.createElement("textarea");
    textarea.value = sshKey.public_key;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (loading) return null;

  return (
    <div className="max-w-lg">
      <h2 className="text-lg font-semibold text-gray-900 mb-1">Git SSH Key</h2>
      <p className="text-sm text-gray-500 mb-4">
        This key lets you use git inside notebook sessions to push and pull from GitHub.
        After generating a key, add the public key to your GitHub account.
      </p>

      {message && (
        <div className="mb-4 p-3 rounded bg-green-50 border border-green-200 text-green-700 text-sm">
          {message}
        </div>
      )}

      {sshKey?.configured && sshKey.public_key ? (
        <div className="space-y-3">
          <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
            <h3 className="text-sm font-semibold text-blue-800 mb-2">Public Key</h3>
            <pre className="text-xs font-mono bg-white border rounded p-3 overflow-x-auto whitespace-pre-wrap break-all">
              {sshKey.public_key}
            </pre>
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleCopy}
                className="px-3 py-1.5 text-sm bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
              >
                {copied ? "Copied" : "Copy Public Key"}
              </button>
              <a
                href="https://github.com/settings/ssh/new"
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1.5 text-sm bg-gray-900 text-white rounded hover:bg-gray-800"
              >
                Add to GitHub
              </a>
            </div>
          </div>

          <p className="text-xs text-gray-500">
            This key is automatically available inside your notebook sessions.
            Use <code className="bg-gray-100 px-1 rounded">git clone</code>, <code className="bg-gray-100 px-1 rounded">git push</code>, etc. from the terminal.
          </p>

          <button
            onClick={handleGenerate}
            disabled={generating}
            className="text-sm text-gray-500 hover:text-gray-700 underline"
          >
            {generating ? "Generating..." : "Regenerate key"}
          </button>
        </div>
      ) : (
        <div className="p-4 bg-gray-50 border border-gray-200 rounded-lg">
          <p className="text-sm text-gray-600 mb-3">
            No SSH key configured. Generate one to use git inside notebook sessions.
          </p>
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="px-4 py-2 bg-bioaf-600 text-white text-sm rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {generating ? "Generating..." : "Generate SSH Key"}
          </button>
        </div>
      )}
    </div>
  );
}

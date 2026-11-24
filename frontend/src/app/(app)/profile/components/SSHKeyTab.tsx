"use client";

import { useConfirm } from "@/hooks/useConfirm";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";
import { Button } from "@/components/ui/Button";

export function SSHKeyTab() {
  const toast = useToast();
  const [sshKey, setSSHKey] = useState<{ configured: boolean; public_key: string | null } | null>(null);
  const confirm = useConfirm();
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState("");
  const [copied, setCopied] = useState(false);
  const [refusal, setRefusal] = useState("");

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
        message:
          "This cannot be undone. Afterwards, add the new public key to GitHub and download the new key " +
          "for work node login. A new key cannot log in to a work node launched with the old one.",
        confirmLabel: "Replace",
        variant: "danger",
      });
      if (!ok) return;
    }
    setGenerating(true);
    setMessage("");
    setRefusal("");
    try {
      const data = await api.post<{ public_key: string; message: string }>("/api/auth/me/ssh-key/generate");
      setSSHKey({ configured: true, public_key: data.public_key });
      setMessage(data.message);
    } catch (err) {
      // A 409 means work nodes may still need the current key: it is unchanged and still downloadable.
      if (err instanceof ApiError && err.status === 409) setRefusal(err.message);
      else setMessage(err instanceof Error ? err.message : "Failed to generate key");
    }
    setGenerating(false);
  }

  async function handleDownload() {
    try {
      await api.download("/api/auth/me/ssh-key/private");
    } catch (e) {
      logError("downloading the SSH key", e);
      toast.error("Your SSH key could not be downloaded. Try again.");
    }
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
        This key lets you use git inside notebook sessions to push and pull from GitHub, and log in to your
        own work nodes. After generating a key, add the public key to your GitHub account.
      </p>

      {refusal && (
        <div role="alert" className="mb-4 p-3 rounded bg-amber-50 border border-amber-200 text-amber-800 text-sm">
          {refusal}
        </div>
      )}

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

          <div className="p-4 bg-gray-50 border border-gray-200 rounded-lg space-y-2">
            <h3 className="text-sm font-semibold text-gray-800">Work node login</h3>
            <p className="text-xs text-gray-600">
              Download your key to log in to your own work nodes from this computer. It also gives access to your
              GitHub repositories, so keep it private.
            </p>
            <Button onClick={handleDownload}>Download key</Button>
            <p className="text-xs text-gray-600">Save it, then restrict its permissions and connect:</p>
            <pre className="text-xs font-mono bg-white border rounded p-2 whitespace-pre-wrap break-all">
              {"mv ~/Downloads/bioaf_id_rsa ~/.ssh/bioaf_id_rsa\nchmod 600 ~/.ssh/bioaf_id_rsa\nssh -i ~/.ssh/bioaf_id_rsa <username>@<work node address>"}
            </pre>
            <p className="text-xs text-gray-500">
              Lost the file? Download it again; the key does not change.
            </p>
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
          <Button
            onClick={handleGenerate}
            disabled={generating}>
            {generating ? "Generating..." : "Generate SSH Key"}
          </Button>
        </div>
      )}
    </div>
  );
}

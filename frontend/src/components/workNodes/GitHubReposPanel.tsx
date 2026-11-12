"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { useToast } from "@/components/shared/Toast";
import { Alert } from "@/components/shared/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { GitHubRepo } from "@/lib/types";

/**
 * The repositories cloned into a work node at boot. The list belongs to the page (the launch and
 * detail dialogs read it too); this panel adds and removes entries and asks the page to re-read.
 */
export function GitHubReposPanel({
  repos,
  canManage,
  onChanged,
}: {
  repos: GitHubRepo[];
  canManage: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const addGuard = useSubmitGuard();

  async function add() {
    if (!url.trim()) return;
    setError(null);
    try {
      await api.post("/api/v1/github-repos", {
        git_ssh_url: url.trim(),
        display_name: name.trim() || null,
      });
      setUrl("");
      setName("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add repo");
    }
  }

  async function remove(repoId: number) {
    try {
      await api.delete(`/api/v1/github-repos/${repoId}`);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete the repository.");
    }
  }

  return (
    <div className="mb-6">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-gray-900"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        GitHub Repos ({repos.length})
      </button>
      {open && (
        <div className="mt-2 bg-white rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-gray-500 mb-3">Add GitHub repos to clone into your work nodes at boot. Provide the git SSH URL from GitHub.</p>

          {error && <Alert tone="error" className="mb-3">{error}</Alert>}

          {canManage && (
            <form
              aria-label="Add GitHub repo"
              onSubmit={(e) => {
                e.preventDefault();
                void addGuard.run(add);
              }}
              className="flex gap-2 mb-3"
            >
              <Input aria-label="Git SSH URL"
                type="text"
                width="auto"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="git@github.com:owner/repo.git"
                className="flex-1 font-mono"
              />
              <Input aria-label="Display name (optional)"
                type="text"
                width="auto"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Display name (optional)"
                className="w-48"
              />
              <Button type="submit" size="sm" disabled={!url.trim()} busy={addGuard.busy} busyLabel="Adding...">
                Add
              </Button>
            </form>
          )}

          {repos.length === 0 ? (
            <p className="text-xs text-gray-500">No repos configured yet.</p>
          ) : (
            <div className="space-y-1">
              {repos.map((repo) => (
                <div key={repo.id} className="flex items-center justify-between py-1.5 px-2 bg-gray-50 rounded text-sm">
                  <div>
                    <span className="font-medium">{repo.display_name}</span>
                    <span className="text-gray-500 font-mono text-xs ml-2">{repo.git_ssh_url}</span>
                  </div>
                  {canManage && (
                    <button
                      onClick={() => void remove(repo.id)}
                      className="text-red-500 hover:text-red-700 text-xs"
                    >
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

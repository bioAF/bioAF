"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { api, ApiError } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { formatRecordedTime } from "@/lib/recordedTime";

/** Settings > System Settings > Security and SSH access (plan 03B). */

interface RangeEntry {
  id?: string | null;
  name: string;
  cidr: string;
}

interface Draft {
  version: number;
  enabled: boolean;
  ranges: RangeEntry[];
}

interface Applied {
  revision: number;
  enabled: boolean;
  ranges: RangeEntry[];
  applied_at?: string | null;
}

interface WorkNodeSection {
  draft: Draft;
  applied: Applied;
  applied_at?: string | null;
  can_roll_back: boolean;
}

interface Recovery {
  status: "not_checked" | "configured_automatic" | "login_tested" | "needs_attention";
  problems: string[];
  sources: { kind: string; source: string; priority?: number; ports: string }[];
  checked_at: string | null;
  login_tested_at?: string | null;
}

interface Restore {
  url: string | null;
  instruction: string | null;
  command: string | null;
  server: { provider: string; account: string; zone: string; instance: string; instance_id: string } | null;
  os_username?: string | null;
  address_choice?: string;
  endpoint_id?: string;
  message: string | null;
}

interface HostSection {
  filter: { draft: Draft; applied: Applied; can_roll_back: boolean; scope: string };
  recovery: Recovery;
  recovery_caption: string;
  restore: Restore;
}

interface SshAccess {
  work_nodes?: WorkNodeSection;
  application_host?: HostSection;
}

const BASE = "/api/v1/settings/ssh-access";

const RECOVERY_LABELS: Record<Recovery["status"], string> = {
  not_checked: "Not checked yet",
  configured_automatic: "Configured (automatic checks)",
  login_tested: "Login tested",
  needs_attention: "Needs attention",
};

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message || fallback : fallback;
}

function RangeEditor({
  draft,
  onChange,
  toggleLabel,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  toggleLabel: string;
}) {
  const [suggestion, setSuggestion] = useState("");
  const update = (index: number, field: "name" | "cidr", value: string) =>
    onChange({ ...draft, ranges: draft.ranges.map((r, i) => (i === index ? { ...r, [field]: value } : r)) });

  const suggestThisComputer = async () => {
    try {
      const observed = await api.get<{ address: string | null; message: string | null }>(`${BASE}/observed-address`);
      if (observed.address) {
        onChange({ ...draft, enabled: true, ranges: [...draft.ranges, { name: "This computer", cidr: observed.address }] });
        setSuggestion(
          "This suggests the address bioAF can see for this computer. Enter a network range instead if you want to allow other addresses.",
        );
      } else {
        setSuggestion(observed.message || "bioAF could not see this computer's address. Enter it yourself.");
      }
    } catch (e) {
      logError("reading this computer's address", e);
      setSuggestion("bioAF could not see this computer's address. Enter it yourself.");
    }
  };

  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => onChange({ ...draft, enabled: e.target.checked })} />
        {toggleLabel}
      </label>
      {draft.ranges.map((r, i) => (
        <div key={r.id ?? `new-${i}`} className="flex gap-2">
          <input
            aria-label="Name"
            className="w-40 rounded border px-2 py-1 text-sm"
            value={r.name}
            onChange={(e) => update(i, "name", e.target.value)}
          />
          <input
            aria-label="Address or range"
            className="flex-1 rounded border px-2 py-1 font-mono text-sm"
            value={r.cidr}
            onChange={(e) => update(i, "cidr", e.target.value)}
          />
          <button
            type="button"
            className="text-sm text-gray-500 underline"
            onClick={() => onChange({ ...draft, ranges: draft.ranges.filter((_, j) => j !== i) })}
          >
            Remove
          </button>
        </div>
      ))}
      <div className="flex gap-3 text-sm">
        <button type="button" className="underline" onClick={() => onChange({ ...draft, ranges: [...draft.ranges, { name: "", cidr: "" }] })}>
          Add a range
        </button>
        <button type="button" className="underline" onClick={() => void suggestThisComputer()}>
          Use this computer&apos;s address
        </button>
      </div>
      {suggestion && <p className="text-xs text-gray-600">{suggestion}</p>}
    </div>
  );
}

function PolicyActions({
  draft,
  savedVersion,
  canRollBack,
  path,
  onSaved,
  onApplied,
  checkPath,
  onChecked,
}: {
  draft: Draft;
  savedVersion: number;
  canRollBack: boolean;
  path: string;
  onSaved: (d: Draft) => void;
  onApplied: () => void;
  checkPath: string;
  onChecked: (result: unknown) => void;
}) {
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const run = async (label: string, fn: () => Promise<void>) => {
    setStatus("");
    setError("");
    try {
      await fn();
    } catch (e) {
      logError(label, e);
      setError(errorText(e, "That did not work. Your current settings are unchanged."));
    }
  };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          onClick={() =>
            void run("saving SSH access changes", async () => {
              const saved = await api.put<{ draft: Draft }>(`${path}/draft`, {
                enabled: draft.enabled,
                ranges: draft.ranges.map((r) => ({ id: r.id ?? null, name: r.name, cidr: r.cidr })),
                base_version: savedVersion,
              });
              onSaved(saved.draft);
              setStatus("Changes saved. They take effect when you apply them.");
            })
          }
        >
          Save changes
        </Button>
        <Button
          variant="secondary"
          onClick={() =>
            void run("checking SSH access", async () => {
              onChecked(await api.post(checkPath));
            })
          }
        >
          Check access
        </Button>
        <Button
          onClick={() =>
            void run("applying SSH access changes", async () => {
              await api.post(`${path}/apply`, { draft_version: savedVersion });
              setStatus("Changes applied.");
              onApplied();
            })
          }
        >
          Apply changes
        </Button>
        {canRollBack && (
          <Button
            variant="secondary"
            onClick={() =>
              void run("rolling back SSH access", async () => {
                await api.post(`${path}/rollback`);
                setStatus("The previous setting was restored.");
                onApplied();
              })
            }
          >
            Roll back
          </Button>
        )}
      </div>
      {status && (
        <p role="status" className="text-sm text-green-700">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

function WorkNodeCard({ section, reload }: { section: WorkNodeSection; reload: () => void }) {
  const [draft, setDraft] = useState<Draft>(section.draft);
  const [savedVersion, setSavedVersion] = useState(section.draft.version);
  const [messages, setMessages] = useState<string[]>([]);
  useEffect(() => {
    setDraft(section.draft);
    setSavedVersion(section.draft.version);
  }, [section.draft]);
  return (
    <Card className="max-w-3xl space-y-4 p-6">
      <section aria-labelledby="work-node-ssh">
        <h2 id="work-node-ssh" className="text-lg font-semibold">
          Work node SSH login
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          Restrict SSH by IP address: from inside a range scientists can log in with a password or key; from elsewhere,
          key only. It applies to work nodes launched after you apply it. It does not limit which networks can reach SSH.
        </p>
        <p className="mt-1 text-xs text-gray-500">
          Applied revision {section.applied.revision}
          {section.applied_at ? `, ${formatRecordedTime(section.applied_at, "datetime", { missing: "" })}` : ""}.{" "}
          {section.applied.enabled && section.applied.ranges.length
            ? `In force: ${section.applied.ranges.map((r) => r.cidr).join(", ")}.`
            : "No range is in force: password or key from anywhere."}
        </p>
        <div className="mt-3">
          <RangeEditor draft={draft} onChange={setDraft} toggleLabel="Restrict SSH by IP address" />
        </div>
        <div className="mt-3">
          <PolicyActions
            draft={draft}
            savedVersion={savedVersion}
            canRollBack={section.can_roll_back}
            path={`${BASE}/work-nodes`}
            checkPath={`${BASE}/work-nodes/check`}
            onSaved={(d) => {
              setDraft(d);
              setSavedVersion(d.version);
            }}
            onApplied={reload}
            onChecked={(r) => setMessages((r as { messages: string[] }).messages ?? [])}
          />
        </div>
        {messages.length > 0 && (
          <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
            {messages.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
      </section>
    </Card>
  );
}

function HostCard({ section, reload }: { section: HostSection; reload: () => void }) {
  const [draft, setDraft] = useState<Draft>(section.filter.draft);
  const [savedVersion, setSavedVersion] = useState(section.filter.draft.version);
  const [recovery, setRecovery] = useState<Recovery>(section.recovery);
  useEffect(() => {
    setDraft(section.filter.draft);
    setSavedVersion(section.filter.draft.version);
    setRecovery(section.recovery);
  }, [section]);
  const restore = section.restore;
  return (
    <Card className="max-w-3xl space-y-4 p-6">
      <section aria-labelledby="host-ssh">
        <h2 id="host-ssh" className="text-lg font-semibold">
          Application server SSH
        </h2>
        <p className="mt-1 text-sm text-gray-600">{section.filter.scope}</p>
        <p className="mt-1 text-xs text-gray-500">
          Applied revision {section.filter.applied.revision}.{" "}
          {section.filter.applied.enabled
            ? `SSH is limited to ${section.filter.applied.ranges.map((r) => r.cidr).join(", ")} plus cloud recovery.`
            : "No bioAF filter is in force; your cloud firewall decides who can reach SSH."}
        </p>
        <div className="mt-3">
          <RangeEditor draft={draft} onChange={setDraft} toggleLabel="Restrict SSH by IP address" />
        </div>

        <h3 className="mt-4 text-sm font-semibold">Cloud recovery access</h3>
        <p className="text-sm text-gray-700">{section.recovery_caption}</p>
        <p className="text-sm">
          <span className="font-medium">{RECOVERY_LABELS[recovery.status]}</span>
          {recovery.status === "login_tested" && recovery.login_tested_at
            ? ` (${formatRecordedTime(recovery.login_tested_at, "datetime", { missing: "" })})`
            : ""}
        </p>
        {recovery.sources.length > 0 && (
          <ul className="text-xs text-gray-600">
            {recovery.sources.map((s) => (
              <li key={`${s.kind}-${s.source}`}>
                {s.kind === "gcp_iap" ? "Google Cloud IAP" : "EC2 Instance Connect"}: {s.source} ({s.ports}
                {s.priority ? `, priority ${s.priority}` : ""})
              </li>
            ))}
          </ul>
        )}
        {recovery.problems.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-sm text-amber-800">
            {recovery.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        <div className="mt-3">
          <PolicyActions
            draft={draft}
            savedVersion={savedVersion}
            canRollBack={section.filter.can_roll_back}
            path={`${BASE}/host`}
            checkPath={`${BASE}/host/check`}
            onSaved={(d) => {
              setDraft(d);
              setSavedVersion(d.version);
            }}
            onApplied={reload}
            onChecked={(r) => setRecovery((r as { recovery: Recovery }).recovery)}
          />
        </div>

        <h3 className="mt-4 text-sm font-semibold">Restore SSH access</h3>
        {restore.url ? (
          <div className="space-y-2 text-sm">
            <a href={restore.url} target="_blank" rel="noopener noreferrer" className="inline-block rounded bg-bioaf-600 px-3 py-1.5 text-white">
              Restore SSH access
            </a>
            <p>{restore.instruction}</p>
            {restore.command && <pre className="whitespace-pre-wrap break-all rounded bg-gray-900 p-2 font-mono text-xs text-green-400">{restore.command}</pre>}
            {restore.address_choice && (
              <p className="text-xs text-gray-600">
                Choose {restore.address_choice}
                {restore.endpoint_id ? ` and the endpoint ${restore.endpoint_id}` : ""}; username {restore.os_username}.
              </p>
            )}
            {restore.server && (
              <p className="text-xs text-gray-500">
                Server {restore.server.instance} in {restore.server.zone}, {restore.server.provider === "gcp" ? "project" : "account"}{" "}
                {restore.server.account}. Bookmark this page; it works while bioAF is down.
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-700">{restore.message}</p>
        )}
      </section>
    </Card>
  );
}

export default function SshAccessPage() {
  const [data, setData] = useState<SshAccess | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.get<SshAccess>(BASE));
      setLoadFailed(false);
    } catch (e) {
      logError("loading SSH access settings", e);
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="flex-1 space-y-6 overflow-y-auto p-6">
      <div>
        <h1 className="mb-2 text-2xl font-bold">Security and SSH access</h1>
        <p data-testid="page-description" className="text-sm text-gray-500">
          Optional SSH login and network settings for work nodes and the application server, and how to get back in
          through your cloud account.
        </p>
      </div>
      {loadFailed && (
        <p role="alert" className="text-sm text-red-700">
          SSH access settings could not be loaded. The technical detail is in the application logs.
        </p>
      )}
      {data?.work_nodes && <WorkNodeCard section={data.work_nodes} reload={() => void load()} />}
      {data?.application_host && <HostCard section={data.application_host} reload={() => void load()} />}
    </main>
  );
}

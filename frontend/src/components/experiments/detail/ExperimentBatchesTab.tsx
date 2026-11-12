"use client";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Input } from "@/components/ui/Input";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { BatchWorkspace } from "./useBatchWorkspace";

export function ExperimentBatchesTab({ workspace }: { workspace: BatchWorkspace }) {
  const { form, setForm, sampleBatches, sequencingBatches } = workspace;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Sample Batches */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Sample Batches</h3>
          <Button size="sm" onClick={() => workspace.setShowForm(!workspace.showForm)}>
            Create Sample Batch
          </Button>
        </div>

        {workspace.showForm && (
          <form
            aria-label="Create sample batch"
            onSubmit={(e) => {
              e.preventDefault();
              void workspace.submit();
            }}
            className="bg-surface rounded-lg shadow p-4 mb-4"
          >
            <div className="grid grid-cols-2 gap-3">
              <Field label="Batch Name *">{(control) => <Input {...control} required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
              <Field label="Prep Date">{(control) => <Input {...control} type="date" value={form.prep_date ?? ""} onChange={(e) => setForm({ ...form, prep_date: e.target.value || null })} />}</Field>
              <Field label="Notes" className="col-span-2">{(control) => <Input {...control} value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value || null })} />}</Field>
            </div>
            <div className="flex gap-2 mt-3">
              <Button size="sm" type="submit" busy={workspace.busy} busyLabel="Saving...">Save</Button>
              <Button size="sm" variant="secondary" onClick={() => workspace.setShowForm(false)}>Cancel</Button>
            </div>
          </form>
        )}

        <div className="grid gap-3">
          {sampleBatches.map((b) => (
            <div key={b.id} className="bg-surface rounded-lg shadow p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="font-semibold">{b.name}</h4>
                  <p className="text-sm text-ink-subtle">{b.sample_count} samples</p>
                </div>
                <div className="text-sm text-ink-subtle">
                  {b.prep_date && <span>Prep: {b.prep_date}</span>}
                </div>
              </div>
              {b.notes && <p className="text-sm text-ink-subtle mt-2">{b.notes}</p>}
            </div>
          ))}
          {sampleBatches.length === 0 && (
            <div className="bg-surface rounded-lg shadow p-6 text-center text-ink-subtle text-sm">No sample batches yet</div>
          )}
        </div>
      </div>

      {/* Sequencing Batches */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Sequencing Batches</h3>
        <div className="grid gap-3">
          {sequencingBatches.map((sb) => {
            const progress = sb.expected_file_count ? Math.round((sb.ingested_file_count / sb.expected_file_count) * 100) : 0;

            return (
              <div key={sb.id} className="bg-surface rounded-lg shadow p-4">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <h4 className="font-semibold">{sb.code}</h4>
                    {sb.instrument_model && <p className="text-sm text-ink-subtle">{sb.instrument_model}</p>}
                  </div>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass("sampleBatch", sb.status)}`}>
                    {sb.status.replace("_", " ")}
                  </span>
                </div>
                {sb.expected_file_count && (
                  <div className="mt-2">
                    <div className="flex justify-between text-xs text-ink-subtle mb-1">
                      <span>Files: {sb.ingested_file_count}/{sb.expected_file_count}</span>
                      <span>{progress}%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-1.5">
                      <div
                        className={`h-1.5 rounded-full ${sb.status === "complete" ? "bg-green-500" : sb.status === "failed" ? "bg-red-500" : "bg-blue-500"}`}
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  </div>
                )}
                {sb.manifest_received_at && (
                  <p className="text-xs text-ink-subtle mt-2">Received: {new Date(sb.manifest_received_at).toLocaleString()}</p>
                )}
              </div>
            );
          })}
          {sequencingBatches.length === 0 && (
            <div className="bg-surface rounded-lg shadow p-6 text-center text-ink-subtle text-sm">No sequencing batches linked to this experiment</div>
          )}
        </div>
      </div>
    </div>
  );
}

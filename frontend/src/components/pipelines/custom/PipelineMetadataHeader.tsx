"use client";

import { Button } from "@/components/ui/Button";
import type { CustomPipelineDetail } from "@/lib/types";

export interface MetadataDraft {
  name: string;
  description: string;
}

/**
 * The pipeline's name, description and key, with its actions. While `draft` is set the name and
 * description are being edited; the draft is the page's, so a reload of the pipeline leaves it be.
 */
export function PipelineMetadataHeader({
  pipeline,
  draft,
  onDraftChange,
  saving,
  error,
  onEdit,
  onSave,
  onCancel,
  canLaunch,
  canEdit,
  canDelete,
  onLaunch,
  onDelete,
}: {
  pipeline: CustomPipelineDetail;
  draft: MetadataDraft | null;
  onDraftChange: (draft: MetadataDraft) => void;
  saving: boolean;
  error: string | null;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  canLaunch: boolean;
  canEdit: boolean;
  canDelete: boolean;
  onLaunch: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="bg-white rounded-lg shadow mb-6">
      <div className="p-6 flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {draft ? (
            <div className="space-y-3">
              <input aria-label="Edit name"
                value={draft.name}
                onChange={(e) => onDraftChange({ ...draft, name: e.target.value })}
                className="w-full text-xl font-bold border rounded px-3 py-1.5"
              />
              <textarea aria-label="Description"
                value={draft.description}
                onChange={(e) => onDraftChange({ ...draft, description: e.target.value })}
                rows={2}
                placeholder="Description"
                className="w-full border rounded px-3 py-2 text-sm"
              />
              {error && <p className="text-sm text-red-600">{error}</p>}
              <div className="flex gap-2">
                <button
                  onClick={onSave}
                  disabled={saving || !draft.name.trim()}
                  className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
                <button onClick={onCancel} className="border px-4 py-1.5 rounded text-sm">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <h1 className="text-2xl font-bold truncate">{pipeline.name}</h1>
              {pipeline.description && (
                <p className="text-sm text-gray-500 mt-1">{pipeline.description}</p>
              )}
              <p className="text-xs text-gray-500 mt-2 font-mono">
                {pipeline.pipeline_key}
              </p>
            </>
          )}
        </div>
        {!draft && (
          <div className="flex items-center gap-2 flex-shrink-0">
            {canLaunch && <Button onClick={onLaunch}>Launch</Button>}
            {canEdit && (
              <button onClick={onEdit} className="border px-4 py-2 rounded-md text-sm">
                Edit
              </button>
            )}
            {canDelete && (
              <button onClick={onDelete} className="text-red-500 text-sm px-2 py-2 hover:underline">
                Delete
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

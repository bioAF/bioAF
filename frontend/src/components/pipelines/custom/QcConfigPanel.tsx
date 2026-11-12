"use client";

import { useState } from "react";
import { readQcConfig } from "./versionDraft";

export function QcConfigPanel({
  qcTemplate,
  qcConfigJsonText,
  onChangeTemplate,
  onChangeConfig,
}: {
  qcTemplate: string;
  qcConfigJsonText: string;
  onChangeTemplate: (value: string) => void;
  onChangeConfig: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);

  // The same reading the page applies before sending, shown while typing.
  const reading = readQcConfig(qcConfigJsonText);
  const parseError = reading.ok ? null : reading.reason === "not_object" ? "Must be a JSON object" : reading.message;

  return (
    <div className="border border-gray-200 rounded">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-gray-700"
      >
        <span>QC dashboard config (optional)</span>
        <span className="text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 space-y-3">
          <p className="text-xs text-gray-500">
            Pipelines that emit a <code className="bg-gray-100 px-1 rounded">qc_metrics.json</code>{" "}
            into <code className="bg-gray-100 px-1 rounded">/outputs/</code> can drive a custom QC
            dashboard. Choose a template (typically <code className="bg-gray-100 px-1 rounded">custom</code>)
            and paste a JSON render config describing sections, metric labels, and thresholds. Both
            fields are versioned with the pipeline -- changing them creates a new pipeline version.
          </p>

          <div>
            <label htmlFor="qc-template" className="text-sm text-gray-500 block mb-1">QC template</label>
            <select id="qc-template"
              value={qcTemplate}
              onChange={(e) => onChangeTemplate(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm bg-white"
            >
              <option value="">(none -- skip QC dashboard)</option>
              <option value="custom">custom</option>
              <option value="scrnaseq">scrnaseq</option>
              <option value="bulk_rnaseq">bulk_rnaseq</option>
            </select>
          </div>

          <div>
            <label htmlFor="qc-config-json" className="text-sm text-gray-500 block mb-1">
              QC config JSON {qcTemplate === "custom" ? "(required for custom layouts)" : "(optional override)"}
            </label>
            <textarea id="qc-config-json"
              value={qcConfigJsonText}
              onChange={(e) => onChangeConfig(e.target.value)}
              rows={10}
              spellCheck={false}
              placeholder={`{\n  "template": "custom",\n  "sections": [{ "id": "main", "metrics": ["my_metric"] }],\n  "metrics": { "my_metric": { "label": "My Metric", "format": "decimal" } }\n}`}
              className="w-full border rounded px-3 py-2 text-xs font-mono"
            />
            {parseError && (
              <p className="text-xs text-red-600 mt-1">JSON: {parseError}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

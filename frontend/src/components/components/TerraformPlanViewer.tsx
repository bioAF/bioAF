"use client";

import type { TerraformRun } from "@/lib/types";
import { Card } from "@/components/ui/Card";

interface TerraformPlanViewerProps {
  planSummary: TerraformRun["plan_summary"];
  onApply: () => void;
  onCancel: () => void;
}

export function TerraformPlanViewer({ planSummary, onApply, onCancel }: TerraformPlanViewerProps) {
  if (!planSummary) {
    return (
      <Card>
        <p className="text-gray-500">No plan available</p>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-lg font-semibold mb-4">Terraform Plan</h2>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="text-center p-3 bg-green-50 rounded">
          <div className="text-2xl font-bold text-green-700">+{planSummary.add_count}</div>
          <div className="text-xs text-green-600">to add</div>
        </div>
        <div className="text-center p-3 bg-yellow-50 rounded">
          <div className="text-2xl font-bold text-yellow-700">~{planSummary.change_count}</div>
          <div className="text-xs text-yellow-600">to change</div>
        </div>
        <div className="text-center p-3 bg-red-50 rounded">
          <div className="text-2xl font-bold text-red-700">-{planSummary.destroy_count}</div>
          <div className="text-xs text-red-600">to destroy</div>
        </div>
      </div>

      {planSummary.add.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-green-700 mb-2">Resources to create:</h3>
          <ul className="text-sm space-y-1">
            {planSummary.add.map((r, i) => (
              <li key={i} className="text-gray-600">
                <span className="text-green-600">+</span> {r.type}.{r.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {planSummary.change.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-yellow-700 mb-2">Resources to modify:</h3>
          <ul className="text-sm space-y-1">
            {planSummary.change.map((r, i) => (
              <li key={i} className="text-gray-600">
                <span className="text-yellow-600">~</span> {r.type}.{r.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {planSummary.destroy.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-red-700 mb-2">Resources to destroy:</h3>
          <ul className="text-sm space-y-1">
            {planSummary.destroy.map((r, i) => (
              <li key={i} className="text-gray-600">
                <span className="text-red-600">-</span> {r.type}.{r.name}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-3 mt-6">
        <button
          onClick={onApply}
          className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
        >
          Apply Changes
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
        >
          Cancel
        </button>
      </div>
    </Card>
  );
}

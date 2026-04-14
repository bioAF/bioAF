"use client";

interface TerraformRun {
  id: number;
  action: string;
  module_name: string | null;
  status: string;
  resources_planned: number | null;
  resources_completed: number;
  triggered_by_user_id: number;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
  plan_json: object | null;
  terraform_state_url: string | null;
}

interface TerraformRunHistoryProps {
  runs: TerraformRun[];
}

const STATUS_COLORS: Record<string, string> = {
  completed: "text-green-700 bg-green-50",
  failed: "text-red-700 bg-red-50",
  planning: "text-blue-700 bg-blue-50",
  applying: "text-blue-700 bg-blue-50",
  awaiting_confirmation: "text-amber-700 bg-amber-50",
  cancelled: "text-gray-700 bg-gray-50",
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function TerraformRunHistory({ runs }: TerraformRunHistoryProps) {
  if (runs.length === 0) {
    return (
      <div data-testid="run-history-empty" className="text-sm text-gray-500 py-4">
        No Terraform operations recorded yet.
      </div>
    );
  }

  return (
    <table data-testid="run-history-table" className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b text-left text-gray-500">
          <th className="py-2 pr-4 font-medium">ID</th>
          <th className="py-2 pr-4 font-medium">Action</th>
          <th className="py-2 pr-4 font-medium">Module</th>
          <th className="py-2 pr-4 font-medium">Status</th>
          <th className="py-2 pr-4 font-medium">Resources</th>
          <th className="py-2 font-medium">Started</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} className="border-b hover:bg-gray-50 align-top">
            <td className="py-2 pr-4 text-gray-400">#{run.id}</td>
            <td className="py-2 pr-4 font-mono text-xs">{run.action}</td>
            <td className="py-2 pr-4 text-gray-600">{run.module_name ?? "-"}</td>
            <td className="py-2 pr-4">
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[run.status] ?? "text-gray-700 bg-gray-50"}`}
              >
                {run.status}
              </span>
              {run.status === "failed" && run.error_message && (
                <p className="mt-1 text-xs text-red-600 max-w-md break-words">{run.error_message}</p>
              )}
            </td>
            <td className="py-2 pr-4 text-gray-600">
              {run.resources_planned !== null
                ? `${run.resources_completed}/${run.resources_planned}`
                : "-"}
            </td>
            <td className="py-2 text-gray-500 text-xs">{formatDate(run.started_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

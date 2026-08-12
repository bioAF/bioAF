import type { PipelineRunPreflight } from "@/lib/types";

/** Why this pipeline cannot run with the selected samples, shown while the user
 *  can still do something about it.
 *
 *  The same checks run server-side at launch, so this never invents a verdict;
 *  it renders the one the API already gave. Before it existed, five of the
 *  twenty most popular pipelines failed only after the user had clicked through
 *  the whole wizard and pressed Launch. */
export function LaunchBlockedNotice({ preflight }: { preflight: PipelineRunPreflight | null }) {
  if (!preflight || preflight.can_launch) return null;

  const missing = preflight.details?.missing_columns;

  return (
    <div className="mb-6 p-3 border border-red-200 bg-red-50 rounded" role="alert">
      <h3 className="font-medium text-sm text-red-800 mb-1">This run cannot start</h3>
      <p className="text-xs text-gray-700 mb-2">{preflight.reason}</p>

      {missing && (
        <ul className="space-y-2">
          {Object.entries(missing).map(([column, info]) => (
            <li key={column} className="text-xs text-gray-700">
              <span className="font-medium">{column.replace(/_/g, " ")}</span>
              {info.sample_field && (
                <>
                  {" comes from each sample's "}
                  <span className="font-medium">{info.sample_field.replace(/_/g, " ")}</span>
                  {", which is empty for:"}
                </>
              )}
              {!info.sample_field && " is not something bioAF can derive. Missing for:"}
              <div className="mt-0.5 text-gray-600">
                {info.samples.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
              </div>
              {info.allowed_values.length > 0 && (
                <div className="mt-0.5 text-gray-600">Allowed values: {info.allowed_values.join(", ")}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

import type { PipelineRunPreflight } from "@/lib/types";

/** Reasons where a value is PRESENT and wrong, rather than absent. They render
 *  their own explanation, so the "missing for:" wording below must not also run:
 *  telling someone who just typed a value that it is missing sends them to look
 *  for the wrong problem. */
const VALUE_REASONS = new Set(["invalid_characters", "collision", "not_unique"]);

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
              {/* Characters the pipeline will not take. bioAF does not rename
                  anything: it says which value was refused and offers a spelling
                  that would work, and the scientist decides whether to take it.
                  Where nothing would work no suggestion is shown, because a
                  mistyped accession cannot be repaired by punctuation. */}
              {info.reason === "invalid_characters" && (
                <>
                  {" will not accept these values:"}
                  <ul className="mt-0.5 ml-4 list-disc text-gray-600">
                    {info.samples.map((s) => (
                      <li key={s.id}>
                        <span className="font-mono">{s.value ?? s.external_id}</span>
                        {s.suggestion && (
                          <>
                            {" (suggested: "}
                            <span className="font-mono font-medium text-gray-900">{s.suggestion}</span>
                            {")"}
                          </>
                        )}
                        {s.external_id && s.value !== s.external_id && <> on {s.external_id}</>}
                      </li>
                    ))}
                  </ul>
                  {info.pattern && (
                    <div className="mt-0.5 text-gray-600">
                      It accepts values matching <span className="font-mono">{info.pattern}</span>
                    </div>
                  )}
                </>
              )}

              {/* Two different samples that would be written under one name. The
                  sheet would merge their results, so this is never resolved by
                  suggesting a name. */}
              {info.reason === "collision" && (
                <>
                  {" would give two samples the same name, which would merge their results. Rename one of:"}
                  <div className="mt-0.5 text-gray-600">
                    {info.samples.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
                  </div>
                </>
              )}

              {/* A column the pipeline uses to tell two rows apart, which would
                  repeat. "Missing" is wrong here and sends the scientist to
                  supply one value for every sample, when what is needed is a
                  value that DIFFERS between the repeated rows. A sample
                  sequenced over two lanes is the ordinary way to arrive here. */}
              {info.reason === "not_unique" && (
                <>
                  {info.unique_with && info.unique_with.length > 0 ? (
                    <>
                      {" has to differ between rows that share the same "}
                      <span className="font-medium">{info.unique_with.join(" and ").replace(/_/g, " ")}</span>
                      {", and more than one row would carry the same pair for:"}
                    </>
                  ) : (
                    " has to be different in every row, and more than one row would repeat it for:"
                  )}
                  <div className="mt-0.5 text-gray-600">
                    {info.samples.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
                  </div>
                </>
              )}

              {/* A column the pipeline requires only because another one is
                  filled. Naming the trigger is what makes it answerable: the
                  schema's own required list does not mention this column, so
                  "it is missing" sends the user looking for a rule that is not
                  there. */}
              {!VALUE_REASONS.has(info.reason ?? "") && (
                <>
                  {info.required_by && (
                    <>
                      {" is required because these samples carry "}
                      <span className="font-medium">{info.required_by.replace(/_/g, " ")}</span>
                      {":"}
                    </>
                  )}
                  {!info.required_by && info.sample_field && (
                    <>
                      {" comes from each sample's "}
                      <span className="font-medium">{info.sample_field.replace(/_/g, " ")}</span>
                      {", which is empty for:"}
                    </>
                  )}
                  {!info.required_by && !info.sample_field && " is not something bioAF can derive. Missing for:"}
                  <div className="mt-0.5 text-gray-600">
                    {info.samples.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
                  </div>
                </>
              )}
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

import type { PipelineRunPreflight } from "@/lib/types";

/** Reasons where a value is PRESENT and wrong, rather than absent. They render
 *  their own explanation, so the "missing for:" wording below must not also run:
 *  telling someone who just typed a value that it is missing sends them to look
 *  for the wrong problem. */
const VALUE_REASONS = new Set(["invalid_characters", "collision", "not_unique", "empty_in_row"]);

/** "1", "1 and 2", "1, 2 and 3". A scientist reads these as a sentence, and
 *  "lanes 1,2" reads as a value they are supposed to type somewhere. */
function listOf(items: string[]): string {
  if (items.length < 2) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Whether the summary sentence above the list has already stated the remedy.
 *
 *  The summary and the per-column detail were written for the same fact by
 *  different hands, and both render, so the one-row-per-sample block said its
 *  rule and its remedy twice, one line under the other. A block that repeats
 *  itself reads as a rendering mistake at the moment the scientist is deciding
 *  whether to trust what bioAF says about their data.
 *
 *  It is conditional because the summary can only name a remedy when every gap
 *  carries the SAME one: `_blocked_summary` falls back to "no value would
 *  separate them" where they differ, and trimming the detail there would leave
 *  nobody saying what to do. Same rule, so the block neither stutters nor goes
 *  silent. */
function summaryNamesTheRemedy(missing: Record<string, { remedy?: string | null }>): boolean {
  const remedies = new Set(Object.values(missing).map((info) => info.remedy ?? null));
  return remedies.size === 1 && !remedies.has(null);
}

/** Why this pipeline cannot run with the selected samples, shown while the user
 *  can still do something about it.
 *
 *  The same checks run server-side at launch, so this never invents a verdict;
 *  it renders the one the API already gave. Before it existed, five of the
 *  twenty most popular pipelines failed only after the user had clicked through
 *  the whole wizard and pressed Launch. */
export function LaunchBlockedNotice({
  preflight,
  onDropSamplesWithoutFiles,
}: {
  preflight: PipelineRunPreflight | null;
  /** Take the remedy this block offers: leave the file-less samples out and ask
   *  again. Optional, because a caller with nowhere to put the answer must not
   *  be shown a button that does nothing. */
  onDropSamplesWithoutFiles?: () => void;
}) {
  if (!preflight || preflight.can_launch) return null;

  const missing = preflight.details?.missing_columns;
  const withoutFiles = preflight.details?.samples_without_files ?? [];
  // Only what the summary cannot say belongs in the detail below it. The
  // summary has to be on screen for that to hold, so an absent one keeps the
  // detail whole rather than trusting it to have spoken.
  const alreadySaid = Boolean(preflight.reason) && !!missing && summaryNamesTheRemedy(missing);

  return (
    <div className="mb-6 p-3 border border-red-200 bg-red-50 rounded" role="alert">
      <h3 className="font-medium text-sm text-red-800 mb-1">This run cannot start</h3>
      <p className="text-xs text-gray-700 mb-2">{preflight.reason}</p>

      {/* Samples carrying no input file at all. Not a missing COLUMN: there is
          no value to type, so this names them and offers the only thing that
          moves the run forward, which is leaving them out. The launch has always
          offered exactly this, from a dialog reached by pressing Launch. The
          preflight now blocks first (issue #85), which disables that button, so
          the offer has to stand here or it stands nowhere. */}
      {withoutFiles.length > 0 && (
        <div className="text-xs text-gray-700">
          <p>This pipeline reads a file per sample, and these have none attached:</p>
          <div className="mt-0.5 text-gray-600">
            {withoutFiles.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
          </div>
          {/* No count in the label. The wizard sends a null sample list when the
              whole experiment is selected, so "continue with 11" would be a
              number bioAF is guessing at. */}
          {onDropSamplesWithoutFiles && (
            <button
              type="button"
              onClick={onDropSamplesWithoutFiles}
              className="mt-2 border border-red-200 bg-white text-red-800 font-medium px-3 py-1.5 rounded text-xs hover:bg-red-100"
            >
              Drop them and continue
            </button>
          )}
        </div>
      )}

      {missing && (
        <ul className="space-y-2">
          {Object.entries(missing).map(([column, info]) => (
            <li key={column} className="text-xs text-gray-700">
              {/* The column leads every other sentence here. It must NOT lead
                  this one: the column is the sample's own name, and naming it
                  first is exactly what sent scientists to rename their sample,
                  which corrupts the LIMS record and still does not launch. */}
              {info.remedy !== "one_row_per_sample" && (
                <span className="font-medium">{column.replace(/_/g, " ")}</span>
              )}
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
              {/* These rows came off ONE sequencing run and differ only by
                  lane, so no value could separate them: any that did would be a
                  lane wearing a run's name. bioAF refused to write that itself,
                  and must not ask the scientist to write it either. The remedy
                  is the reads, or a different pipeline. */}
              {info.reason === "not_unique" && info.remedy === "merge_reads" && (
                <>
                  {alreadySaid
                    ? " would repeat across:"
                    : " cannot tell these rows apart, because they came off one sequencing run:"}
                  <ul className="mt-0.5 ml-4 list-disc text-gray-600">
                    {(info.repeated ?? []).map((entry) => (
                      <li key={`${entry.source}:${entry.run}`}>
                        {entry.source === "flowcell" ? "flow cell " : "run "}
                        <span className="font-mono">{entry.run}</span>
                        {entry.lanes.length > 0 && (
                          <>
                            {entry.lanes.length > 1 ? ", lanes " : ", lane "}
                            {listOf(entry.lanes)}
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                  {!alreadySaid && (
                    <div className="mt-0.5 text-gray-600">
                      Merge those reads into one pair per sample, or choose a pipeline that reads a lane.
                    </div>
                  )}
                </>
              )}

              {/* ampliseq's rule is on the sample's own name ALONE, so no value
                  of any other column can ever separate two of its rows. */}
              {info.reason === "not_unique" && info.remedy === "one_row_per_sample" && (
                <>
                  {alreadySaid
                    ? "More than one set of reads:"
                    : "This pipeline takes one row per sample, and these have more than one set of reads:"}
                  <div className="mt-0.5 text-gray-600">
                    {info.samples.map((s) => s.external_id || `sample ${s.id}`).join(", ")}
                  </div>
                  {!alreadySaid && (
                    <div className="mt-0.5 text-gray-600">
                      Merge those reads, or launch them as separate samples.
                    </div>
                  )}
                </>
              )}

              {/* The ordinary case: bioAF has no value for this column and the
                  scientist may well have one, so it is still asked for. */}
              {info.reason === "not_unique" && !info.remedy && (
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

              {/* A row about to be emitted with this column empty. The sample
                  HAS reads, so "bioAF cannot derive this" would send the
                  scientist to fill in a field when what is absent is a FILE for
                  one of its rows. A sample sequenced over two lanes, where one
                  lane lost a mate, is the ordinary way to arrive here. */}
              {info.reason === "empty_in_row" && (
                <>
                  {" would be empty in a row this sheet is about to write, which the pipeline requires. Check the files attached to:"}
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

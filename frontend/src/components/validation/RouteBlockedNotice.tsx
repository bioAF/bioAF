/**
 * The one case where choosing the route at the button still needs a person.
 *
 * The choice is taken before the paper is read, so it cannot know what the paper actually has. When
 * the driver discovers after the read that the chosen route is impossible, it holds the study at
 * `plan_ready` and records why. Without this notice that hold is invisible: the study simply stops,
 * which is the exact defect asking upfront was meant to remove.
 *
 * Plain on screen, technical in the logs, per [[ui-errors-plain-in-app-technical-in-logs]].
 */

const ROUTE_LABEL: Record<string, string> = {
  deposit: "Deposited data and available code",
  pipeline: "Raw reads",
  both: "Both routes",
};

/**
 * change_7.2 section 1: which of the four independent questions refused the route, in one line a
 * reader can act on.
 *
 * A missing adapter is a limit of bioAF and must never read as an omission by the authors. A missing
 * input is a fact about the deposit and must never read as a software limitation. A missing
 * authorization is a fact about this organization and must never read as a feature request. Sending
 * a lab to negotiate data access for a capability gap, or to file a feature request for a permission
 * problem, is what one shared wording does.
 */
const ACTION_HEADLINE: Record<string, string> = {
  no_adapter: "bioAF cannot read this archive yet. The data is published; the limitation is ours.",
  no_input: "The deposit holds nothing this route could reproduce from.",
  not_authorized: "This organisation is not authorised to reach this dataset.",
  undetermined: "bioAF could not establish what this paper published.",
  contested: "The plan and the deposit disagree about what this data is.",
};

export function RouteBlockedNotice({
  blocked,
}: {
  blocked?: { chosen?: string | null; reason?: string | null; action?: string | null } | null;
}) {
  if (!blocked?.reason) return null;
  const label = ROUTE_LABEL[blocked.chosen ?? ""] ?? blocked.chosen;
  const headline = ACTION_HEADLINE[blocked.action ?? ""];

  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-3">
      <p className="text-sm font-medium text-amber-900">
        {label ? `"${label}" is not available for this paper` : "The route you chose is not available"}
      </p>
      {headline && <p className="mt-1 text-xs font-medium text-amber-900">{headline}</p>}
      <p className="mt-1 text-xs text-amber-900">{blocked.reason}</p>
      <p className="mt-1 text-xs text-amber-800">
        The study is waiting and nothing has been spent. You can choose a different route below.
      </p>
    </div>
  );
}

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

export function RouteBlockedNotice({
  blocked,
}: {
  blocked?: { chosen?: string | null; reason?: string | null } | null;
}) {
  if (!blocked?.reason) return null;
  const label = ROUTE_LABEL[blocked.chosen ?? ""] ?? blocked.chosen;

  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-3">
      <p className="text-sm font-medium text-amber-900">
        {label ? `"${label}" is not available for this paper` : "The route you chose is not available"}
      </p>
      <p className="mt-1 text-xs text-amber-900">{blocked.reason}</p>
      <p className="mt-1 text-xs text-amber-800">
        The study is waiting and nothing has been spent. You can choose a different route below.
      </p>
    </div>
  );
}

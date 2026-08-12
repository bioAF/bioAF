import type { RegistryPipeline } from "@/lib/types";

/** The install / update control for one row of the nf-core registry browser.
 *
 *  A pipeline with no `latest_release` has never cut a release (nf-core/
 *  scdownstream, 19th most-starred, has only a `dev` branch). Its version list
 *  comes back empty and it cannot be installed, so offering Install sends the
 *  user through a fetch that can only end in "No released versions available."
 *  Say so up front instead. */
export function RegistryInstallAction({
  pipeline,
  canInstall,
  onInstall,
  onUpdate,
}: {
  pipeline: RegistryPipeline;
  canInstall: boolean;
  onInstall: (p: RegistryPipeline) => void;
  onUpdate: (p: RegistryPipeline) => void;
}) {
  const unreleased = !pipeline.latest_release;

  return (
    <>
      {pipeline.update_available && canInstall && (
        <button
          onClick={() => onUpdate(pipeline)}
          className="text-sm px-3 py-1 rounded bg-amber-600 text-white hover:bg-amber-700"
        >
          Update to v{pipeline.latest_release}
        </button>
      )}
      {!pipeline.installed && canInstall && !pipeline.archived && !unreleased && (
        <button
          onClick={() => onInstall(pipeline)}
          className="text-sm px-3 py-1 rounded bg-bioaf-600 text-white hover:bg-bioaf-700"
        >
          Install
        </button>
      )}
      {!pipeline.installed && canInstall && !pipeline.archived && unreleased && (
        <span className="text-xs text-gray-500" title="This pipeline has not published a release yet">
          No release yet
        </span>
      )}
      {pipeline.installed && !pipeline.update_available && (
        <span className="text-xs text-gray-500">Latest installed</span>
      )}
      {!canInstall && !pipeline.installed && <span className="text-xs text-gray-500">View only</span>}
    </>
  );
}

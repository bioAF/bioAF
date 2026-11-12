"use client";

import { useState } from "react";

/** What a custom pipeline's code can rely on at run time. Open by default. */
export function RuntimeContractPanel() {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-blue-200 bg-blue-50 rounded">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-blue-900"
      >
        <span>Runtime contract: what your code can expect</span>
        <span className="text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 text-xs text-blue-900 space-y-2">
          <div>
            <span className="font-semibold">Inputs:</span> selected files are staged
            under <code className="bg-white/60 px-1 rounded">/data/</code> using paths
            like <code className="bg-white/60 px-1 rounded">/data/&lt;project&gt;/&lt;experiment&gt;/&lt;sample&gt;/&lt;filename&gt;</code>.
            Full mapping (file ids, project, experiment, sample) is in{" "}
            <code className="bg-white/60 px-1 rounded">/data/manifest.json</code>.
          </div>
          <div>
            <span className="font-semibold">Variables:</span> launch-time values are
            written to <code className="bg-white/60 px-1 rounded">/data/params.json</code>{" "}
            and exposed as environment variables{" "}
            <code className="bg-white/60 px-1 rounded">PARAM_&lt;NAME&gt;</code> on the
            container.
          </div>
          <div>
            <span className="font-semibold">Code source:</span> a GitHub repo is cloned
            to <code className="bg-white/60 px-1 rounded">/code/&lt;repo-display-name&gt;/</code>{" "}
            (the working directory). A code blob is written to{" "}
            <code className="bg-white/60 px-1 rounded">/code/script</code>. Inline
            commands run with <code className="bg-white/60 px-1 rounded">/data</code> as
            the working directory.
          </div>
          <div>
            <span className="font-semibold">Outputs:</span> anything your script writes
            to <code className="bg-white/60 px-1 rounded">/outputs/</code> is synced to
            GCS and registered as File records on the run after completion.
          </div>
          <div>
            <span className="font-semibold">Logs:</span> by default the run page tails
            pod stdout/stderr. If you set a log file path below (e.g.{" "}
            <code className="bg-white/60 px-1 rounded">/outputs/my-script.log</code>),
            that file replaces the default view post-completion. The OOM detector
            (SIGKILL/exit 137) is automatic.
          </div>
          <div>
            <span className="font-semibold">Entrypoint:</span> the entrypoint command
            below runs from the working directory above. Use the same form you would in
            a shell: for example, <code className="bg-white/60 px-1 rounded">bash run.sh</code>{" "}
            or <code className="bg-white/60 px-1 rounded">python main.py</code>.
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

/**
 * The three routes a validation can take, as one chooser used in two places.
 *
 * It is asked at the "Validate findings" button (owner decision, 2026-09-08), where nothing about
 * the paper is known yet, and again at the C1 gate for a study that reached `plan_ready` without a
 * choice. Those two callers differ only in whether they can pass `capabilities`: discovery needs the
 * GEO accession, which only exists once the paper has been read. One component so the wording and
 * the cost warnings cannot drift apart between them.
 *
 * Domain wording is deliberate per [[keep-domain-biotech-terminology]]: "Deposited data" and "Raw
 * reads" are what a bioinformatician calls these.
 */

export type ValidationRoute = "deposit" | "pipeline" | "both";

export interface CapabilityAnswer {
  value?: string | null;
  failure_reason?: string | null;
}

export interface RouteCapabilities {
  preprocessed_data?: CapabilityAnswer | null;
  raw_data?: CapabilityAnswer | null;
}

/**
 * The note under a route, or null when there is nothing to say.
 *
 * plan_7 amendment 3: the three answers mean three different things. YES enables the route silently,
 * NO says it cannot work and why, and UNKNOWN says we could not establish it and STILL offers the
 * route. Rendering UNKNOWN as NO would hide a workable route behind a GEO timeout.
 */
export function capabilityNote(
  answer: CapabilityAnswer | null | undefined,
  absentMessage: string,
): string | null {
  if (!answer?.value) return null;
  if (answer.value === "no") return absentMessage;
  if (answer.value === "unknown") {
    return answer.failure_reason
      ? `We could not check this: ${answer.failure_reason}`
      : "We could not check whether this is available, so this route may not work.";
  }
  return null;
}

export function RouteChooser({
  route,
  onChange,
  capabilities,
  idPrefix = "route",
}: {
  route: ValidationRoute;
  onChange: (r: ValidationRoute) => void;
  /** Omitted at the button, where the paper has not been read yet. */
  capabilities?: RouteCapabilities | null;
  idPrefix?: string;
}) {
  const depositNote = capabilityNote(
    capabilities?.preprocessed_data,
    "No pre-processed data was found in this study's GEO deposit, so this route has nothing to reproduce from.",
  );
  const rawNote = capabilityNote(
    capabilities?.raw_data,
    "No raw sequencing reads are published for this study, so this route has nothing to fetch.",
  );

  const options: { value: ValidationRoute; label: string; body: string; note: string | null }[] = [
    {
      value: "deposit",
      label: "Deposited data and available code",
      body:
        "Reproduces the paper's analysis from the processed data it deposited in GEO, using the study's own differential design. Validates the computational findings. Takes minutes. It cannot detect a processing error, a swapped sample or a contaminated library, because the upstream processing is not repeated.",
      note: depositNote,
    },
    {
      value: "pipeline",
      label: "Raw reads",
      body:
        "Fetches the sequencing reads and re-runs the whole analysis. Validates the pre-processing and sample quality. Takes hours.",
      note: rawNote,
    },
    {
      value: "both",
      label: "Both",
      body:
        "Runs each route as its own study, so each carries its own verdict. The deposited run starts now; the raw-reads run starts alongside it.",
      note: null,
    },
  ];

  return (
    <div className="space-y-3">
      {options.map((o) => (
        <label key={o.value} className="flex items-start gap-2">
          <input
            type="radio"
            name={`${idPrefix}-validation-route`}
            className="mt-1"
            checked={route === o.value}
            onChange={() => onChange(o.value)}
          />
          <span>
            <span className="font-medium">{o.label}</span>
            <span className="block text-xs text-gray-600">{o.body}</span>
            {o.note && <span className="block text-xs text-amber-800">{o.note}</span>}
          </span>
        </label>
      ))}

      {route !== "deposit" && (
        <p className="text-xs text-amber-800">
          The raw-reads route spends compute on your cloud account, and the spend cannot be recovered
          once the run starts.
        </p>
      )}
    </div>
  );
}

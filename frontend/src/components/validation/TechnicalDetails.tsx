/**
 * change_7.3 decision 8: technical detail sits in a collapsed element under the plain statement.
 *
 * The HTTP status, URL, exception class, attempt count and times are what an administrator needs to
 * act on a failure, and they are noise to the scientist reading the report. The statement above
 * stays plain; this is one click away, and the same detail still goes to the logs.
 */

const KEY_LABEL: Record<string, string> = {
  url: "URL",
  http_status: "HTTP status",
  error_class: "Error class",
  outcome: "What happened",
  attempts: "Attempts",
  first_at: "First attempt",
  last_at: "Last attempt",
  source: "Source",
  recorded_reason: "Recorded reason",
  ledger: "Ledger entries",
  cause: "Cause",
};

export function TechnicalDetails({
  detail,
  summary = "Technical details",
}: {
  detail: Record<string, unknown> | null | undefined;
  summary?: string;
}) {
  const rows = Object.entries(detail ?? {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (rows.length === 0) return null;
  return (
    <details className="mt-1 text-xs text-gray-500">
      <summary className="cursor-pointer select-none">{summary}</summary>
      <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
        {rows.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-gray-500">{KEY_LABEL[key] ?? key}</dt>
            <dd className="break-all font-mono text-gray-600">
              {Array.isArray(value) ? value.join(", ") : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

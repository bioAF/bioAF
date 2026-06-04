"use client";

export type SessionBucket = "active" | "recent" | "all";

export const SESSION_BUCKETS: { value: SessionBucket; label: string; description: string }[] = [
  {
    value: "active",
    label: "Active",
    description: "Sessions still pending, starting, running, idle, or stopping",
  },
  {
    value: "recent",
    label: "Recent",
    description: "Anything launched in the last 24 hours",
  },
  {
    value: "all",
    label: "All",
    description: "Every session your role can see",
  },
];

interface SessionBucketFilterProps {
  value: SessionBucket;
  onChange: (next: SessionBucket) => void;
}

export function SessionBucketFilter({ value, onChange }: SessionBucketFilterProps) {
  return (
    <div role="group" aria-label="Filter sessions" className="inline-flex rounded-md shadow-sm">
      {SESSION_BUCKETS.map((bucket, idx) => {
        const selected = bucket.value === value;
        const rounded = idx === 0 ? "rounded-l-md" : idx === SESSION_BUCKETS.length - 1 ? "rounded-r-md" : "";
        return (
          <button
            key={bucket.value}
            type="button"
            aria-pressed={selected}
            title={bucket.description}
            onClick={() => {
              if (!selected) onChange(bucket.value);
            }}
            className={
              "px-3 py-1.5 text-sm border border-gray-300 -ml-px first:ml-0 " +
              rounded +
              " " +
              (selected
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 hover:bg-gray-50")
            }
          >
            {bucket.label}
          </button>
        );
      })}
    </div>
  );
}

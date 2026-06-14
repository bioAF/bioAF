// Provider-appropriate object-storage display strings (stage 8). Keyed by the
// storage backend the install resolves to (from /stack-options): gcs on GCP,
// s3 on AWS. Anything else (nfs/unknown/undefined) falls back to GCS so a GCP
// install renders exactly as before.

export interface StorageDisplay {
  label: string; // "GCS" / "S3"
  uriScheme: string; // "gs://" / "s3://"
  cliCopy: string; // "gsutil cp" / "aws s3 cp"
}

export function storageDisplay(backend: string | undefined): StorageDisplay {
  if (backend === "s3") {
    return { label: "S3", uriScheme: "s3://", cliCopy: "aws s3 cp" };
  }
  return { label: "GCS", uriScheme: "gs://", cliCopy: "gsutil cp" };
}

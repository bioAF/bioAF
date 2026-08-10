"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useStackOptions } from "@/hooks/useStackOptions";
import { storageDisplay, type StorageDisplay } from "@/lib/storageDisplay";
import { AutoIngestControls } from "./AutoIngestControls";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";
import { Button } from "@/components/ui/Button";

interface BucketMetrics {
  bucket_name: string;
  purpose: string;
  size_bytes: number;
  object_count: number;
  storage_class: string;
  versioning_enabled: boolean;
  lifecycle_rules: string[];
  created_at: string | null;
}

interface BucketMetricsResponse {
  buckets: BucketMetrics[];
}

interface StorageSectionProps {
  storageDeployed: boolean;
  terraformInitialized: boolean;
  pubsubConfigured?: boolean;
  onDeploy: () => void;
  onUpdateStorage?: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <button
      onClick={handleCopy}
      aria-label="Copy"
      className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-50 text-gray-600"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function IngestGuidancePanel({
  bucket,
  storage,
}: {
  bucket: BucketMetrics;
  storage: StorageDisplay;
}) {
  const objectUri = `${storage.uriScheme}${bucket.bucket_name}/`;
  const uploadCmd = `${storage.cliCopy} your_file.fastq.gz ${objectUri}`;

  return (
    <div className="mt-3 p-3 bg-teal-50 border border-teal-200 rounded text-sm space-y-2">
      <p className="text-teal-800 font-medium text-xs uppercase tracking-wide">
        How to send data to bioAF
      </p>
      <p className="text-xs text-teal-700">
        Upload files to this {storage.label} bucket. bioAF automatically detects
        new files, parses their filenames, and catalogs them.
      </p>
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-600">Bucket:</span>
        <code className="text-xs bg-white border border-teal-200 rounded px-2 py-1 font-mono text-gray-800">
          {bucket.bucket_name}
        </code>
        <CopyButton text={bucket.bucket_name} />
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-xs bg-white border border-teal-200 rounded px-2 py-1 font-mono text-gray-800 truncate">
          {uploadCmd}
        </code>
        <CopyButton text={uploadCmd} />
      </div>
      <div className="flex gap-4 text-xs text-teal-700">
        <Link
          href="/settings/naming-profiles"
          className="underline hover:text-teal-900"
        >
          Configure naming profiles
        </Link>
        <Link href="/data/browser" className="underline hover:text-teal-900">
          View ingested files
        </Link>
      </div>
    </div>
  );
}

function BucketCard({
  bucket,
  pubsubConfigured,
  onUpdateStorage,
  storage,
}: {
  bucket: BucketMetrics;
  pubsubConfigured?: boolean;
  onUpdateStorage?: () => void;
  storage: StorageDisplay;
}) {
  const isIngest = bucket.purpose === "ingest";
  const cardClass = isIngest
    ? "bg-white rounded-lg border-2 border-teal-400 p-4 shadow-sm"
    : "bg-white rounded-lg border border-gray-200 p-4 shadow-sm";

  return (
    <div
      data-testid={`bucket-card-${bucket.bucket_name}`}
      className={cardClass}
    >
      <div className="flex items-start justify-between mb-1">
        <h3 className="font-mono text-sm font-semibold text-gray-800">
          {bucket.bucket_name}
        </h3>
        {isIngest && (
          <span className="text-xs bg-teal-100 text-teal-700 px-2 py-0.5 rounded-full font-medium">
            Ingest
          </span>
        )}
      </div>
      <p className="text-xs text-gray-500 mb-2 capitalize">
        {bucket.purpose.replace("_", " ")}
      </p>
      <div className="flex gap-4 text-xs text-gray-500">
        <span>{formatBytes(bucket.size_bytes)}</span>
        <span>{bucket.object_count} objects</span>
        <span>{bucket.storage_class}</span>
      </div>
      {bucket.lifecycle_rules.length > 0 && (
        <div className="mt-1 text-xs text-gray-500">
          {bucket.lifecycle_rules.map((rule, i) => (
            <span key={i} className="block">
              {rule}
            </span>
          ))}
        </div>
      )}
      {isIngest && <IngestGuidancePanel bucket={bucket} storage={storage} />}
      {isIngest && (
        <AutoIngestControls
          storageDeployed={true}
          pubsubConfigured={pubsubConfigured ?? false}
          onUpdateStorage={onUpdateStorage}
        />
      )}
    </div>
  );
}

function DeployStorageCard({
  terraformInitialized,
  onDeploy,
  storage,
}: {
  terraformInitialized: boolean;
  onDeploy: () => void;
  storage: StorageDisplay;
}) {
  return (
    <div className="bg-white rounded-lg border-2 border-dashed border-gray-300 p-6 text-center">
      <h3 className="text-lg font-semibold text-gray-700 mb-2">
        Storage Infrastructure
      </h3>
      <p className="text-sm text-gray-500 mb-4">
        Storage infrastructure has not been deployed. Deploy {storage.label}{" "}
        buckets to enable file storage.
      </p>
      {!terraformInitialized && (
        <p className="text-xs text-amber-700 mb-3">
          Terraform must be initialized before deploying storage.{" "}
          <Link
            href="/infrastructure/components"
            className="underline hover:text-amber-900"
          >
            Run bootstrap first
          </Link>
          .
        </p>
      )}
      <Button
        onClick={onDeploy}
        disabled={!terraformInitialized}>
        Deploy Storage
      </Button>
    </div>
  );
}

export function StorageSection({
  storageDeployed,
  terraformInitialized,
  pubsubConfigured,
  onDeploy,
  onUpdateStorage,
}: StorageSectionProps) {
  const toast = useToast();
  const [buckets, setBuckets] = useState<BucketMetrics[]>([]);
  // Provider-appropriate object-storage labels (GCS / S3), resolved from the
  // install's cloud via /stack-options; defaults to GCS so GCP is unchanged.
  const { kubernetesOption } = useStackOptions();
  const storage = storageDisplay(kubernetesOption?.storage_backend);

  useEffect(() => {
    if (!storageDeployed) return;

    api
      .get<BucketMetricsResponse>("/api/v1/infrastructure/storage/buckets")
      .then((data) => setBuckets(data.buckets))
      .catch((e) => {
        logError("loading bucket metrics", e);
        toast.error(loadFailureMessage("Bucket metrics"));
      });
  }, [storageDeployed]);

  if (!storageDeployed) {
    return (
      <DeployStorageCard
        terraformInitialized={terraformInitialized}
        onDeploy={onDeploy}
        storage={storage}
      />
    );
  }

  if (buckets.length === 0) return null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {buckets.map((bucket) => (
          <BucketCard
            key={bucket.bucket_name}
            bucket={bucket}
            pubsubConfigured={pubsubConfigured}
            onUpdateStorage={onUpdateStorage}
            storage={storage}
          />
        ))}
      </div>
    </div>
  );
}

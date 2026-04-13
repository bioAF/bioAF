/** GCP regions and zones available for bioAF deployments. */

export const GCP_REGIONS = [
  "us-central1", "us-east1", "us-east4", "us-west1", "us-west2", "us-west3", "us-west4",
  "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6",
  "asia-east1", "asia-east2", "asia-northeast1", "asia-south1", "asia-southeast1",
];

export const GCP_ZONES: Record<string, string[]> = {
  "us-central1": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
  "us-east1": ["us-east1-b", "us-east1-c", "us-east1-d"],
  "us-east4": ["us-east4-a", "us-east4-b", "us-east4-c"],
  "us-west1": ["us-west1-a", "us-west1-b", "us-west1-c"],
};

export function zonesForRegion(region: string): string[] {
  return GCP_ZONES[region] ?? [`${region}-b`, `${region}-c`, `${region}-d`];
}

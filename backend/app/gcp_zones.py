"""GCP region-to-zone mapping.

Not every GCP region has a "-a" zone (e.g. us-east1 only has b, c, d).
Use these helpers instead of appending "-a" blindly.
"""

GCP_ZONES: dict[str, list[str]] = {
    "us-central1": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
    "us-east1": ["us-east1-b", "us-east1-c", "us-east1-d"],
    "us-east4": ["us-east4-a", "us-east4-b", "us-east4-c"],
    "us-west1": ["us-west1-a", "us-west1-b", "us-west1-c"],
    "us-west2": ["us-west2-a", "us-west2-b", "us-west2-c"],
    "us-west3": ["us-west3-a", "us-west3-b", "us-west3-c"],
    "us-west4": ["us-west4-a", "us-west4-b", "us-west4-c"],
    "europe-west1": ["europe-west1-b", "europe-west1-c", "europe-west1-d"],
    "europe-west2": ["europe-west2-a", "europe-west2-b", "europe-west2-c"],
    "europe-west3": ["europe-west3-a", "europe-west3-b", "europe-west3-c"],
    "europe-west4": ["europe-west4-a", "europe-west4-b", "europe-west4-c"],
    "europe-west6": ["europe-west6-a", "europe-west6-b", "europe-west6-c"],
    "asia-east1": ["asia-east1-a", "asia-east1-b", "asia-east1-c"],
    "asia-east2": ["asia-east2-a", "asia-east2-b", "asia-east2-c"],
    "asia-northeast1": ["asia-northeast1-a", "asia-northeast1-b", "asia-northeast1-c"],
    "asia-south1": ["asia-south1-a", "asia-south1-b", "asia-south1-c"],
    "asia-southeast1": ["asia-southeast1-a", "asia-southeast1-b", "asia-southeast1-c"],
}


def default_zone(region: str) -> str:
    """Return the first available zone for a region."""
    zones = GCP_ZONES.get(region)
    if zones:
        return zones[0]
    return f"{region}-b"


def zones_for_region(region: str) -> list[str]:
    """Return all known zones for a region."""
    return GCP_ZONES.get(region, [f"{region}-b", f"{region}-c", f"{region}-d"])

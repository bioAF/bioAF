"""The release's installation metrics destination (ADR-075).

Public configuration, delivered in the backend image so installers handle no token: the PostHog
project token is a write-only ingestion key that cannot read data back. Importing this module does no
network I/O. ``BIOAF_POSTHOG_PROJECT_TOKEN`` / ``BIOAF_POSTHOG_HOST`` override the pair together, and
``BIOAF_INSTALL_METRICS_DISABLED=1`` disables reporting whatever is configured here.
"""

POSTHOG_PROJECT_TOKEN = "phc_pCS32TftoB4AnR42t3ZYv9Yavvz4tjetFJhSGmftwvkg"
# PostHog US Cloud ingestion.
POSTHOG_HOST = "https://us.i.posthog.com"

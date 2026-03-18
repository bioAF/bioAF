# ADR-028: BigQuery Billing Export for Accurate Cost Data

**Status:** Accepted
**Date:** 2026-03-17
**Deciders:** Brent (repository owner)

---

## Context

bioAF's cost center estimates costs by querying GKE cluster metrics and GCS bucket sizes, then applying static pricing tables. This approach does not account for sustained-use discounts, committed-use discounts, free-tier credits, or actual per-SKU pricing. The result is approximate numbers that diverge from real invoices.

As of 2026-03-16, the cost center and dashboard budget widget are broken entirely (see #123), making this the right time to rebuild on a durable foundation rather than patch the estimation approach.

GCP's BigQuery billing export provides exact, discount-adjusted cost data broken down by service, SKU, and day. It is the only mechanism GCP offers for programmatic access to actual billing data -- there is no REST API or gcloud command that returns "what did I spend this month."

### Constraints discovered during research

- **No programmatic enablement.** Enabling billing export is Console-only. There is no gcloud command, REST API, or reliable Terraform resource for it. This is a known gap in the Terraform Google provider (hashicorp/terraform-provider-google#4848).
- **Data latency.** BQ billing export data lags by several hours (up to 24h for finalized data). Intraday costs are not available from BQ alone.
- **Billing account-level config.** Billing export is configured on the billing account, not the GCP project. Once enabled, it persists independently of project-level infrastructure changes.

---

## Decision

Replace the adapter-based cost estimation with BigQuery billing export as the authoritative cost data source. The setup flow is guided by a modal within the bioAF UI that minimizes manual steps to a single Console action.

### Setup Flow

1. **User initiates setup** from the Cost Center page (or the broken budget widget) via a "Set Up Billing Export" action.
2. **Terraform creates the BigQuery dataset** (`billing_export`) in the user's GCP project automatically. This also grants the bioAF service account `roles/bigquery.dataViewer` on the dataset. No user action required.
3. **Modal displays a direct link** to the GCP Console billing export page (`console.cloud.google.com/billing/export`) with instructions to:
   - Select "Detailed usage cost" export
   - Choose the project containing the `billing_export` dataset
   - Select the `billing_export` dataset
   - Click Save
4. **User clicks "Verify"** in the modal. The backend queries the BQ dataset to confirm the export table exists and data is flowing.
5. **Verification succeeds.** The backend records the billing export as configured and begins querying BQ for cost data.

This is one Console page, one action. Everything else is automated.

### Cost Data Architecture

```text
BQ billing export table
  (gcp_billing_export_v1_XXXXXX)
         |
         v
  CostService.sync_billing_data()
    - Queries BQ for MTD actuals
    - Maps GCP service names to bioAF components:
        Compute Engine  ->  node, compute
        Cloud Storage   ->  storage
        Other services  ->  other (future)
    - Stores in cost_records table
         |
         v
  /api/costs/summary  ->  Cost Center page
  /api/costs/summary  ->  Dashboard budget widget
```

**Intraday gap.** Since BQ data lags up to 24 hours, today's partial costs use the existing adapter-based estimates (GKE metrics + GCS API) as a bridge. Historical and MTD totals come from BQ.

**Local development.** When `BIOAF_COMPUTE_MODE == local`, the mock cost path remains unchanged. BQ queries are only attempted when billing export is verified as configured.

### Durability

- The **BigQuery dataset** is a project-level resource. It survives compute infrastructure teardowns and redeploys (GKE cluster deletes, node pool changes, etc.).
- The **billing export configuration** is at the billing account level. It persists independently of all project-level infrastructure.
- The **cost_records table** in PostgreSQL retains historical data across infrastructure cycles.
- A full project teardown and recreation would require re-running the setup flow (Terraform recreates the dataset, user re-enables export). This is the expected behavior since the project itself is new.

---

## Consequences

**Positive:**

- Cost data reflects actual invoiced amounts, including all discounts and credits
- One-time setup with no ongoing maintenance
- Durable across infrastructure lifecycle (deploy, teardown, redeploy)
- Component breakdown (node, storage, compute) maps directly from GCP service names
- Free to enable; queries fall within BQ's 1 TB/month free tier

**Negative:**

- One manual Console step is unavoidable (GCP does not expose billing export configuration programmatically)
- Data latency of up to 24 hours for finalized costs; intraday requires adapter-based estimation as a bridge
- Initial delay of ~24 hours after enabling export before first data appears
- Requires the user to have Billing Account Administrator or Billing Account Costs Manager role to enable export

---

## References

- #123 -- Cost center menu and budget widget broken
- #111 -- Use GCP BigQuery billing export for accurate cost data
- #85 -- Dashboard: Cost vs. Budget widget shows $0
- ADR-007 -- UI-driven Terraform execution
- [GCP: Set up billing export to BigQuery](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-setup)
- [GCP: Billing export table schema](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables)
- [Terraform provider gap: hashicorp/terraform-provider-google#4848](https://github.com/hashicorp/terraform-provider-google/issues/4848)

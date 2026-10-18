### Dashboard

- Hide cost figures on the dashboard until the billing dashboard is set up.
  Before BigQuery billing export is configured, the Cost vs. Budget and Cost /
  spend trend widgets now say "Please enable the billing dashboard" and link to
  the Cost Center, instead of showing estimated spend that did not match the
  invoice. Once the dataset exists but the export has not been verified, they
  show "Pending" with a "Finish setup in Cost Center" link, and an info icon
  explaining that setup is finished by enabling export in Google Cloud and
  clicking Check Status (data can take up to 24 hours to arrive).
- Remove the two cost widgets from the default admin dashboard. Admins add them
  from the widget picker after setting up the billing dashboard.

### Cost Center

- Rename "Set Up Billing Export" and "Teardown Billing Export" to "Set Up
  Billing Dashboard" and "Teardown Billing Dashboard".

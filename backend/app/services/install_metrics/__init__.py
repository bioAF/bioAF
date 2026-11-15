"""Opt-in installation metrics (ADR-075).

- ``state``: the installation-wide choice, the Installation ID, and the policy that reserves reports.
- ``payload``: the closed allowlist every report is built from and checked against.
- ``outbox``: reserved reports in ``install_metric_events``.
- ``sender``: the bounded periodic delivery to PostHog.
- ``upgrades``: successful host upgrade operations recorded as reports.
"""

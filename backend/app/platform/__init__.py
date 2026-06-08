"""Platform layer: config and credential concerns adapters may depend on.

The BioAF Adapter Layer (BAL) forbids adapters from importing app.services
(the layering inversion). Adapters legitimately need two leaf-ward things:
platform configuration (the key/value platform_config table, including the
Fernet-encrypted service-account key) and GCP credential resolution. Those live
here so both adapters and services can import them without an adapter ever
reaching up into the service layer.

This package imports only app.config, app.database, app.models, and stdlib /
crypto / google-auth helpers. It must never import app.services or app.adapters.
"""

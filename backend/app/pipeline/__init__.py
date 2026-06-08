"""Nextflow / pipeline domain logic shared across the BAL.

This is a leaf layer: pure domain helpers (e.g. Nextflow trace parsing) that
both an adapter (``app.adapters``) and a service (``app.services``) may import
without violating the BAL layering rule. It must never import ``app.adapters``
or ``app.services``.
"""

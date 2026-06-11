"""Shared Kubernetes/GKE plumbing for the BAL adapters.

The compute, notebook, and cellxgene Kubernetes providers inherit from
different base classes (ComputeProvider / NotebookProvider / CellxgeneProvider),
so the shared GKE connect + auth layer lives here as a collaborator
(`GkeConnection`) that each provider composes, rather than a common base.
"""

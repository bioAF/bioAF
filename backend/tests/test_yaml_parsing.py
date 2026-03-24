"""Legacy YAML parsing tests (pre-ADR-033).

The conda YAML parsing and updating functionality has been superseded by
the versioned environment system (ADR-033). Environment definitions are
now stored as raw Dockerfile or conda YAML text in environment_versions,
and managed through the version creation API rather than individual
package operations.
"""

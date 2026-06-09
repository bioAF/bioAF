### Fixes

- Service-account-key GCP credentials now work. When `gcp_credential_source`
  was set to `service_account_key`, the stored key was read without being
  decrypted, so credential loading failed and silently fell back to the VM's
  default service account. The key is now decrypted at read time and actually
  used. Installs using the default `vm_default` credential source are unaffected.

### Internal

- Consolidated all `platform_config` access behind `PlatformConfigService`
  (`get` / `get_many` / `set`, plus a new no-decrypt `export_all` for config
  backups). Removed ~140 raw SQL statements across 44 modules. An architecture
  guard test now fails the build if any module other than the service or the
  key-rotation CLI runs raw `platform_config` SQL, keeping the encrypt/decrypt
  boundary for sensitive keys in one place.

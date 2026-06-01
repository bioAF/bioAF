/** Pure utilities for predicting the next reference version and inferring
 *  the importer's extract mode from a source URL. Kept dependency-free so
 *  they can be unit-tested without React or network. */

export type ExtractMode = "none" | "gzip" | "tar" | "tar.gz";

/** Predict the next version label for a reference, given the existing
 *  versions for that name + category.
 *
 *  Behavior:
 *  - No existing versions -> "v1" (first upload).
 *  - Existing 'v<N>' or bare '<N>' versions -> "v<max+1>".
 *  - All existing versions are non-numeric (e.g., "GRCh38.p14") -> "v1"; the
 *    user can still override the prefill in the form to keep their own
 *    naming convention.
 */
export function predictNextVersion(existingVersions: readonly string[]): string {
  let max = 0;
  let foundNumeric = false;
  for (const raw of existingVersions) {
    const trimmed = raw.trim();
    const match = trimmed.match(/^v?(\d+)$/i);
    if (!match) continue;
    const n = Number(match[1]);
    if (!Number.isFinite(n)) continue;
    foundNumeric = true;
    if (n > max) max = n;
  }
  return foundNumeric ? `v${max + 1}` : "v1";
}

/** Infer the importer's extract mode from a source URL.
 *
 *  Order matters: ".tar.gz" / ".tgz" wins over a plain ".gz" so a tarball
 *  doesn't get treated as a single gzipped blob.
 */
export function extractModeForUrl(url: string): ExtractMode {
  if (!url) return "none";
  // Drop query string + fragment so an upload URL with ?token=... still
  // resolves by its real extension.
  const stripped = url.split("?")[0].split("#")[0].toLowerCase();
  if (stripped.endsWith(".tar.gz") || stripped.endsWith(".tgz")) return "tar.gz";
  if (stripped.endsWith(".tar")) return "tar";
  if (stripped.endsWith(".gz")) return "gzip";
  return "none";
}

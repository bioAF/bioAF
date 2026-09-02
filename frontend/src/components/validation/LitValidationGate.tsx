"use client";

import { useBetaFeatures } from "@/hooks/useBetaFeatures";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

/** Copy shown when someone reaches the validation surface with the flag off. */
export function LitValidationDisabledNotice() {
  return (
    <div className="mx-auto max-w-md rounded-lg border border-gray-200 bg-white p-8 text-center">
      <h2 className="text-lg font-semibold text-gray-900">
        Literature Validation isn&apos;t enabled
      </h2>
      <p className="mt-2 text-sm text-gray-500">
        This is a beta feature and is turned off on this instance. An admin can enable it under
        Settings &rsaquo; Beta Features.
      </p>
    </div>
  );
}

/**
 * Content gate for the lit_validation surface. Mirrors the nav's beta gate so the validation
 * pages are not reachable by direct URL when the flag is off (the entry button is gated the same
 * way in ValidatePaperButton). Default-denies while the flag state loads, so nothing flashes.
 */
export function LitValidationGate({ children }: { children: React.ReactNode }) {
  const { flags, loading } = useBetaFeatures();
  if (loading) return <LoadingSpinner />;
  if (!flags.lit_validation) return <LitValidationDisabledNotice />;
  return <>{children}</>;
}

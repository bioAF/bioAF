"use client";

import { NetworkingSettingsContent } from "@/components/settings/NetworkingSettingsContent";
import { OrganizationCertificateUpload } from "@/components/settings/OrganizationCertificateUpload";
import { usePermissions } from "@/hooks/usePermissions";

export default function NetworkingSettingsPage() {
  const { canAccess, loading } = usePermissions();
  return (
    <main className="flex-1 overflow-y-auto p-6">
      <NetworkingSettingsContent />
      {!loading && canAccess("infrastructure", "configure") && (
        <div className="mt-6 max-w-3xl">
          <OrganizationCertificateUpload />
        </div>
      )}
    </main>
  );
}

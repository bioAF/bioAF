"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { NamingProfileDetail } from "@/components/naming/NamingProfileDetail";
import { NamingProfileWizard } from "@/components/naming/NamingProfileWizard";
import type { NamingProfile } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";

export default function SettingsNamingProfilesPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [profiles, setProfiles] = useState<NamingProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);
  const [editingProfile, setEditingProfile] = useState<NamingProfile | null>(null);
  const [detailProfile, setDetailProfile] = useState<NamingProfile | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("infrastructure", "configure")) {
      router.push("/dashboard");
      return;
    }
    loadProfiles();
  }, [router, permLoading, canAccess]);

  const loadProfiles = async () => {
    try {
      const data = await api.get<NamingProfile[]>(
        "/api/naming-profiles?status=active",
      );
      setProfiles(data);
    } catch {
      setError("Failed to load naming profiles");
    } finally {
      setLoading(false);
    }
  };

  const handleSaved = () => {
    setShowWizard(false);
    setEditingProfile(null);
    setMessage("Profile saved.");
    loadProfiles();
  };

  const openEdit = (p: NamingProfile) => {
    setDetailProfile(null);
    setEditingProfile(p);
    setShowWizard(true);
  };

  const handleDeactivate = async (id: number) => {
    try {
      await api.delete(`/api/naming-profiles/${id}`);
      setMessage("Profile deactivated.");
      await loadProfiles();
    } catch {
      setError("Failed to deactivate profile.");
    }
  };

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Naming Profiles</h1>
          {!showWizard && (
            <button
              onClick={() => setShowWizard(true)}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
            >
              New Profile
            </button>
          )}
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg">{error}</div>
        )}
        {message && (
          <div className="mb-4 p-3 bg-green-50 text-green-700 rounded-lg">{message}</div>
        )}

        {/* Feature breadcrumb */}
        <div className="mb-6 bg-blue-50 border border-blue-200 rounded-lg p-5">
          <h2 className="text-base font-semibold text-blue-900 mb-1">
            How naming profiles work
          </h2>
          <p className="text-sm text-blue-700 mb-3">
            A naming profile tells bioAF how your team encodes
            information in filenames. bioAF <em>reads</em> filenames
            using your profile; it never renames, rewrites, or
            standardizes them.
          </p>
          <p className="text-sm text-blue-700 mb-3">
            Each segment in a filename carries a short letter identifier
            (1-4 letters) that names the field it represents. Segment
            order in the filename does not matter: bioAF recognizes
            segments by their identifier, not their position.
          </p>
          <ul className="text-xs text-blue-800 space-y-1 list-disc list-inside">
            <li>
              <span className="font-medium">Number segment</span>
              {": "}letters plus a zero-padded integer, e.g.{" "}
              <code>SMP0042</code>.
            </li>
            <li>
              <span className="font-medium">String segment</span>
              {": "}letters plus a value separated by the opposite of
              the profile delimiter, e.g. <code>req-bmills</code> with
              delimiter <code>_</code>.
            </li>
            <li>
              <span className="font-medium">Date segment</span>
              {": "}one of <code>YYYYMMDD</code>, <code>YYYY-MM-DD</code>
              , or <code>YYMMDD</code>. No identifier.
            </li>
          </ul>
          <p className="text-sm text-blue-700 mt-3">
            There is no default profile shipped with bioAF. Create your
            team{"'"}s profile to start parsing filenames.
          </p>
        </div>

        {showWizard && (
          <NamingProfileWizard
            onSave={handleSaved}
            onCancel={() => {
              setShowWizard(false);
              setEditingProfile(null);
            }}
            profile={editingProfile ?? undefined}
          />
        )}

        {detailProfile && (
          <NamingProfileDetail
            profile={detailProfile}
            onClose={() => setDetailProfile(null)}
            onEdit={() => openEdit(detailProfile)}
          />
        )}

        {loading ? (
          <ContentLoading />
        ) : profiles.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            No active naming profiles yet. Click{" "}
            <span className="font-medium">New Profile</span> to create
            your team{"'"}s convention.
          </div>
        ) : (
          <div className="bg-white border rounded-lg overflow-hidden">
            <table className="min-w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Name
                  </th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Delimiter
                  </th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Segments
                  </th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th scope="col" className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {profiles.map((p) => (
                  <tr
                    key={p.id}
                    {...clickableRow(() => setDetailProfile(p))}
                    className="hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-6 py-4">
                      <div className="font-medium text-gray-900">{p.name}</div>
                      {p.description && (
                        <div className="text-sm text-gray-500">{p.description}</div>
                      )}
                    </td>
                    <td className="px-6 py-4 font-mono">{p.delimiter}</td>
                    <td className="px-6 py-4 text-sm">
                      {p.segments
                        .map((s) =>
                          s.field_type === "date"
                            ? `[${s.date_format ?? "date"}]`
                            : `${s.identifier ?? "?"}:${s.field_name}`,
                        )
                        .join(` ${p.delimiter} `)}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`px-2 py-1 rounded text-xs font-medium ${
                          p.status === "active"
                            ? "bg-green-100 text-green-700"
                            : "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {p.status}
                      </span>
                    </td>
                    <td
                      className="px-6 py-4 text-right space-x-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {p.status === "active" && (
                        <button
                          onClick={() => handleDeactivate(p.id)}
                          className="text-sm text-red-600 hover:text-red-700"
                        >
                          Deactivate
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}

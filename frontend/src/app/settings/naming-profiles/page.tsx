"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";
import type { NamingProfile, NamingProfileTestResult, SegmentDefinition } from "@/lib/types";

const FIELD_OPTIONS = [
  "date", "project_code", "experiment_code", "sample_id",
  "data_type", "analysis_type", "researcher_initials",
  "version", "organism", "ignore", "custom",
];

export default function SettingsNamingProfilesPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [profiles, setProfiles] = useState<NamingProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [editingProfile, setEditingProfile] = useState<NamingProfile | null>(null);

  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formDelimiter, setFormDelimiter] = useState("_");
  const [formStripExt, setFormStripExt] = useState(true);
  const [formSegments, setFormSegments] = useState<SegmentDefinition[]>([
    { position: 0, field: "project_code", required: true },
  ]);
  const [formProjectMappings, setFormProjectMappings] = useState("");
  const [formExperimentMappings, setFormExperimentMappings] = useState("");

  const [testFilenames, setTestFilenames] = useState("");
  const [testResults, setTestResults] = useState<NamingProfileTestResult[]>([]);

  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("infrastructure", "configure")) { router.push("/dashboard"); return; }
    loadProfiles();
  }, [router, permLoading, canAccess]);

  const loadProfiles = async () => {
    try {
      const data = await api.get<NamingProfile[]>("/api/naming-profiles");
      setProfiles(data);
    } catch {
      setError("Failed to load naming profiles");
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setFormName("");
    setFormDescription("");
    setFormDelimiter("_");
    setFormStripExt(true);
    setFormSegments([{ position: 0, field: "project_code", required: true }]);
    setFormProjectMappings("");
    setFormExperimentMappings("");
    setEditingProfile(null);
  };

  const openEdit = (p: NamingProfile) => {
    setEditingProfile(p);
    setFormName(p.name);
    setFormDescription(p.description || "");
    setFormDelimiter(p.delimiter);
    setFormStripExt(p.strip_extension);
    setFormSegments(p.segments);
    setFormProjectMappings(
      Object.entries(p.project_code_mappings).map(([k, v]) => `${k}=${v}`).join("\n")
    );
    setFormExperimentMappings(
      Object.entries(p.experiment_code_mappings).map(([k, v]) => `${k}=${v}`).join("\n")
    );
    setShowCreate(true);
  };

  const parseMappings = (text: string): Record<string, string> => {
    const mappings: Record<string, string> = {};
    text.split("\n").filter(Boolean).forEach((line) => {
      const [key, value] = line.split("=").map((s) => s.trim());
      if (key && value) mappings[key] = value;
    });
    return mappings;
  };

  const handleSubmit = async () => {
    setError("");
    setMessage("");
    try {
      const body = {
        name: formName,
        description: formDescription || null,
        delimiter: formDelimiter,
        strip_extension: formStripExt,
        segments: formSegments,
        project_code_mappings: parseMappings(formProjectMappings),
        experiment_code_mappings: parseMappings(formExperimentMappings),
      };

      if (editingProfile) {
        await api.put(`/api/naming-profiles/${editingProfile.id}`, body);
        setMessage("Profile updated");
      } else {
        await api.post("/api/naming-profiles", body);
        setMessage("Profile created");
      }
      setShowCreate(false);
      resetForm();
      await loadProfiles();
    } catch {
      setError("Failed to save profile");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/api/naming-profiles/${id}`);
      setMessage("Profile deactivated");
      await loadProfiles();
    } catch {
      setError("Failed to deactivate profile");
    }
  };

  const handleTest = async () => {
    setError("");
    try {
      const filenames = testFilenames.split("\n").filter(Boolean);
      const results = await api.post<NamingProfileTestResult[]>(
        "/api/naming-profiles/test",
        { filenames }
      );
      setTestResults(results);
    } catch {
      setError("Test failed");
    }
  };

  const addSegment = () => {
    setFormSegments([
      ...formSegments,
      { position: formSegments.length, field: "custom", required: false },
    ]);
  };

  const removeSegment = (idx: number) => {
    setFormSegments(
      formSegments.filter((_, i) => i !== idx).map((s, i) => ({ ...s, position: i }))
    );
  };

  const updateSegment = (idx: number, updates: Partial<SegmentDefinition>) => {
    setFormSegments(
      formSegments.map((s, i) => (i === idx ? { ...s, ...updates } : s))
    );
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-6xl mx-auto">
            <div className="flex justify-between items-center mb-6">
              <h1 className="text-2xl font-bold text-gray-900">Naming Profiles</h1>
              <div className="flex gap-2">
                <button
                  onClick={() => { setShowTest(!showTest); setShowCreate(false); }}
                  className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
                >
                  Test Filenames
                </button>
                <button
                  onClick={() => { resetForm(); setShowCreate(!showCreate); setShowTest(false); }}
                  className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
                >
                  New Profile
                </button>
              </div>
            </div>

            {error && <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg">{error}</div>}
            {message && <div className="mb-4 p-3 bg-green-50 text-green-700 rounded-lg">{message}</div>}

            {showTest && (
              <div className="mb-6 bg-white border rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">Test Filenames Against All Active Profiles</h2>
                <textarea
                  value={testFilenames}
                  onChange={(e) => setTestFilenames(e.target.value)}
                  placeholder="Enter filenames, one per line..."
                  className="w-full h-32 border rounded-lg p-3 font-mono text-sm mb-4"
                />
                <button
                  onClick={handleTest}
                  className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
                >
                  Run Test
                </button>
                {testResults.length > 0 && (
                  <div className="mt-4 overflow-x-auto">
                    <table className="min-w-full text-sm">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-4 py-2 text-left">Filename</th>
                          <th className="px-4 py-2 text-left">Status</th>
                          <th className="px-4 py-2 text-left">Profile</th>
                          <th className="px-4 py-2 text-left">Parsed Segments</th>
                        </tr>
                      </thead>
                      <tbody>
                        {testResults.map((r, i) => (
                          <tr key={i} className="border-t">
                            <td className="px-4 py-2 font-mono">{r.filename}</td>
                            <td className="px-4 py-2">
                              <span className={`px-2 py-1 rounded text-xs font-medium ${
                                r.match_status === "matched" ? "bg-green-100 text-green-700" :
                                r.match_status === "unmatched" ? "bg-red-100 text-red-700" :
                                "bg-yellow-100 text-yellow-700"
                              }`}>{r.match_status}</span>
                            </td>
                            <td className="px-4 py-2">{r.profile_name || "—"}</td>
                            <td className="px-4 py-2 font-mono text-xs">
                              {r.parsed_segments ? JSON.stringify(r.parsed_segments) : r.error || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {showCreate && (
              <div className="mb-6 bg-white border rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">
                  {editingProfile ? "Edit Profile" : "New Profile"}
                </h2>
                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                    <input value={formName} onChange={(e) => setFormName(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                    <input value={formDescription} onChange={(e) => setFormDescription(e.target.value)} className="w-full border rounded-lg px-3 py-2" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Delimiter</label>
                    <select value={formDelimiter} onChange={(e) => setFormDelimiter(e.target.value)} className="w-full border rounded-lg px-3 py-2">
                      <option value="_">Underscore (_)</option>
                      <option value="-">Hyphen (-)</option>
                      <option value=".">Dot (.)</option>
                    </select>
                  </div>
                  <div className="flex items-center gap-2 pt-6">
                    <input type="checkbox" checked={formStripExt} onChange={(e) => setFormStripExt(e.target.checked)} id="strip-ext" />
                    <label htmlFor="strip-ext" className="text-sm text-gray-700">Strip file extension</label>
                  </div>
                </div>

                <div className="mb-4">
                  <div className="flex justify-between items-center mb-2">
                    <label className="text-sm font-medium text-gray-700">Segments</label>
                    <button onClick={addSegment} className="text-sm text-bioaf-600 hover:text-bioaf-700">+ Add Segment</button>
                  </div>
                  {formSegments.map((seg, idx) => (
                    <div key={idx} className="flex items-center gap-2 mb-2">
                      <span className="text-xs text-gray-500 w-6">{idx}</span>
                      <select
                        value={seg.field}
                        onChange={(e) => updateSegment(idx, { field: e.target.value as SegmentDefinition["field"] })}
                        className="flex-1 border rounded px-2 py-1 text-sm"
                      >
                        {FIELD_OPTIONS.map((f) => (<option key={f} value={f}>{f}</option>))}
                      </select>
                      {seg.field === "date" && (
                        <select value={seg.format || "YYYY-MM-DD"} onChange={(e) => updateSegment(idx, { format: e.target.value })} className="border rounded px-2 py-1 text-sm">
                          <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                          <option value="YYYYMMDD">YYYYMMDD</option>
                        </select>
                      )}
                      <label className="flex items-center gap-1 text-xs">
                        <input type="checkbox" checked={seg.required} onChange={(e) => updateSegment(idx, { required: e.target.checked })} />
                        Required
                      </label>
                      <button onClick={() => removeSegment(idx)} className="text-red-500 hover:text-red-700 text-sm">Remove</button>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Project Code Mappings (CODE=project_id, one per line)</label>
                    <textarea value={formProjectMappings} onChange={(e) => setFormProjectMappings(e.target.value)} className="w-full h-20 border rounded-lg px-3 py-2 font-mono text-sm" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Experiment Code Mappings (CODE=experiment_id, one per line)</label>
                    <textarea value={formExperimentMappings} onChange={(e) => setFormExperimentMappings(e.target.value)} className="w-full h-20 border rounded-lg px-3 py-2 font-mono text-sm" />
                  </div>
                </div>

                <div className="flex gap-2">
                  <button onClick={handleSubmit} className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700">
                    {editingProfile ? "Update Profile" : "Create Profile"}
                  </button>
                  <button onClick={() => { setShowCreate(false); resetForm(); }} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Default naming scheme */}
            <div className="mb-6 bg-blue-50 border border-blue-200 rounded-lg p-5">
              <h2 className="text-base font-semibold text-blue-900 mb-1">Default Naming Scheme</h2>
              <p className="text-sm text-blue-700 mb-3">
                Applied automatically when files are uploaded with a project or experiment association.
                Custom profiles below can override this for auto-ingest.
              </p>
              <code className="block bg-white border border-blue-100 rounded px-3 py-2 text-sm font-mono text-gray-800 mb-2">
                {"{ProjectCode}_{ExperimentCode}_{SampleID}_{DataType}_{YYYYMMDD}.ext"}
              </code>
              <ul className="text-xs text-blue-800 space-y-1 mt-2">
                <li><span className="font-medium">ProjectCode</span> — first consonant of each word + 2-digit year + counter (e.g. <code>CS26-1</code>). Auto-assigned on project creation.</li>
                <li><span className="font-medium">ExperimentCode</span> — sequential per project (e.g. <code>E001</code>, <code>E002</code>). Auto-assigned on experiment creation.</li>
                <li><span className="font-medium">SampleID</span> — the sample external ID, when a sample association is selected.</li>
                <li><span className="font-medium">DataType</span> — inferred from file extension: FQ (FASTQ), BAM, counts (h5ad), data (CSV/TSV), report (PDF/HTML), plot (PNG/SVG).</li>
                <li><span className="font-medium">YYYYMMDD</span> — date of upload.</li>
                <li className="text-blue-600 italic">Segments without a value are omitted. The rename is advisory — users can keep the original name at upload time.</li>
              </ul>
            </div>

          {loading ? (
              <ContentLoading />
            ) : profiles.length === 0 ? (
              <div className="text-center py-12 text-gray-500">
                No custom naming profiles configured. Create one to override the default scheme for auto-ingest file parsing.
              </div>
            ) : (
              <div className="bg-white border rounded-lg overflow-hidden">
                <table className="min-w-full">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Delimiter</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Segments</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {profiles.map((p) => (
                      <tr key={p.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4">
                          <div className="font-medium text-gray-900">{p.name}</div>
                          {p.description && <div className="text-sm text-gray-500">{p.description}</div>}
                        </td>
                        <td className="px-6 py-4 font-mono">{p.delimiter}</td>
                        <td className="px-6 py-4 text-sm">{p.segments.map((s) => s.field).join(` ${p.delimiter} `)}</td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${
                            p.status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
                          }`}>{p.status}</span>
                        </td>
                        <td className="px-6 py-4 text-right space-x-2">
                          <button onClick={() => openEdit(p)} className="text-sm text-bioaf-600 hover:text-bioaf-700">Edit</button>
                          {p.status === "active" && (
                            <button onClick={() => handleDelete(p.id)} className="text-sm text-red-600 hover:text-red-700">Deactivate</button>
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
      </div>
    </div>
  );
}

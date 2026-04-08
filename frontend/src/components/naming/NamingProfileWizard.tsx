"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

interface EntityOption {
  id: number;
  name: string;
  code: string | null;
}

interface SegmentMapping {
  value: string;
  role: string;
  entityId?: number;
  entityName?: string;
}

const SEGMENT_ROLES = [
  { value: "project_code", label: "Project" },
  { value: "experiment_code", label: "Experiment" },
  { value: "sample_id", label: "Sample" },
  { value: "sample_index", label: "Sample Index (S-number)" },
  { value: "data_type", label: "Data type (e.g. R1, R2, I1)" },
  { value: "date", label: "Date" },
  { value: "version", label: "Version" },
  { value: "ignore", label: "Not important / skip" },
];

interface Props {
  onSave: () => void;
  onCancel: () => void;
}

export function NamingProfileWizard({ onSave, onCancel }: Props) {
  // Phase: pick-file -> pick-delimiter -> walk-segments -> name-and-save
  const [phase, setPhase] = useState<"pick-file" | "pick-delimiter" | "walk-segments" | "name-and-save">("pick-file");
  const [filename, setFilename] = useState("");
  const [delimiter, setDelimiter] = useState("_");
  const [segments, setSegments] = useState<string[]>([]);
  const [mappings, setMappings] = useState<SegmentMapping[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [profileName, setProfileName] = useState("");
  const [projects, setProjects] = useState<EntityOption[]>([]);
  const [experiments, setExperiments] = useState<EntityOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<{ projects: EntityOption[] }>("/api/projects?page_size=200").then((data) => {
      setProjects(data.projects ?? []);
    }).catch(() => {});
    api.get<{ experiments: EntityOption[] }>("/api/experiments?page_size=200").then((data) => {
      setExperiments(data.experiments ?? []);
    }).catch(() => {});
  }, []);

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) {
      setFilename(file.name);
      setPhase("pick-delimiter");
    }
  }

  function handleFilenameSubmit() {
    if (filename.trim()) setPhase("pick-delimiter");
  }

  function stripExtension(name: string): string {
    return name
      .replace(/\.(fastq|fq)(\.gz|\.bz2)?$/i, "")
      .replace(/\.(bam|sam|h5ad|h5|csv|tsv|txt|bed|vcf|gtf|gff|cram)(\.gz)?$/i, "");
  }

  function startWalk() {
    const stripped = stripExtension(filename.trim());
    const parts = stripped.split(delimiter);
    setSegments(parts);
    setMappings(parts.map((value) => ({ value, role: "" })));
    setActiveIndex(0);
    setPhase("walk-segments");
  }

  function assignRole(role: string) {
    setMappings((prev) =>
      prev.map((m, i) => (i === activeIndex ? { ...m, role, entityId: undefined, entityName: undefined } : m))
    );
    // If this role needs entity selection, stay on this segment. Otherwise advance.
    if (role !== "project_code" && role !== "experiment_code" && role !== "sample_id") {
      advanceSegment();
    }
  }

  function assignEntity(entityId: number, entityName: string) {
    setMappings((prev) =>
      prev.map((m, i) => (i === activeIndex ? { ...m, entityId, entityName } : m))
    );
    advanceSegment();
  }

  function advanceSegment() {
    if (activeIndex < segments.length - 1) {
      setActiveIndex((prev) => prev + 1);
    } else {
      setPhase("name-and-save");
    }
  }

  function goBackSegment() {
    if (activeIndex > 0) {
      setActiveIndex((prev) => prev - 1);
      // Clear the current assignment so they can redo it
      setMappings((prev) =>
        prev.map((m, i) => (i === activeIndex - 1 ? { ...m, role: "", entityId: undefined, entityName: undefined } : m))
      );
    }
  }

  async function handleSave() {
    setError("");
    setSaving(true);

    const segmentDefs = mappings.map((m, i) => ({
      position: i,
      field: m.role || "ignore",
      required: m.role !== "ignore" && m.role !== "",
    }));

    const projectMappings: Record<string, string> = {};
    const experimentMappings: Record<string, string> = {};
    for (const m of mappings) {
      if (m.role === "project_code" && m.entityId) {
        projectMappings[m.value] = String(m.entityId);
      }
      if (m.role === "experiment_code" && m.entityId) {
        experimentMappings[m.value] = String(m.entityId);
      }
    }

    try {
      await api.post("/api/naming-profiles", {
        name: profileName || `Profile for ${filename}`,
        delimiter,
        strip_extension: true,
        segments: segmentDefs,
        project_code_mappings: projectMappings,
        experiment_code_mappings: experimentMappings,
      });
      onSave();
    } catch {
      setError("Failed to save profile");
    } finally {
      setSaving(false);
    }
  }

  // Render the filename with the active segment highlighted
  function renderSegmentHighlight() {
    return (
      <div className="flex flex-wrap items-center gap-0.5 font-mono text-lg my-4">
        {segments.map((seg, i) => (
          <span key={i} className="flex items-center">
            {i > 0 && <span className="text-gray-300 mx-0.5">{delimiter}</span>}
            <span
              className={`px-2 py-1 rounded transition-all ${
                i === activeIndex
                  ? "bg-bioaf-100 border-2 border-bioaf-500 text-bioaf-900 font-bold"
                  : i < activeIndex && mappings[i]?.role
                    ? "bg-green-50 border border-green-300 text-green-800"
                    : "bg-gray-100 border border-gray-200 text-gray-400"
              }`}
            >
              {seg}
              {i < activeIndex && mappings[i]?.role && mappings[i].role !== "ignore" && (
                <span className="ml-1 text-xs font-normal text-green-600">
                  ({SEGMENT_ROLES.find((r) => r.value === mappings[i].role)?.label})
                </span>
              )}
            </span>
          </span>
        ))}
      </div>
    );
  }

  const currentMapping = mappings[activeIndex];
  const needsEntity = currentMapping?.role === "project_code" || currentMapping?.role === "experiment_code" || currentMapping?.role === "sample_id";

  return (
    <div className="bg-white border rounded-lg p-6 mb-6">
      {/* Phase 1: Pick a file */}
      {phase === "pick-file" && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Create Naming Profile</h2>
          <p className="text-sm text-gray-600">
            Select a real file or type a filename. The wizard will walk you through
            each part of the name so you can tell bioAF what it means.
          </p>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-sm font-medium text-gray-700 mb-1">Filename</label>
              <input
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleFilenameSubmit(); }}
                placeholder="Type or paste a filename..."
                className="w-full border rounded-lg px-3 py-2 font-mono text-sm"
              />
            </div>
            <span className="text-sm text-gray-400 pb-2">or</span>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
            >
              Choose file
            </button>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={handleFileSelect}
            />
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={handleFilenameSubmit}
              disabled={!filename.trim()}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 disabled:opacity-50"
            >
              Next
            </button>
            <button onClick={onCancel} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Phase 2: Pick delimiter */}
      {phase === "pick-delimiter" && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">How is the filename separated?</h2>
          <p className="font-mono text-sm bg-gray-50 rounded-lg p-3 break-all">{filename}</p>
          <div className="space-y-3">
            {[
              { value: "_", label: "Underscores", example: "part1_part2_part3" },
              { value: "-", label: "Hyphens", example: "part1-part2-part3" },
              { value: ".", label: "Periods", example: "part1.part2.part3" },
            ].map((d) => {
              const stripped = stripExtension(filename.trim());
              const parts = stripped.split(d.value);
              const isSelected = delimiter === d.value;
              return (
                <button
                  key={d.value}
                  onClick={() => setDelimiter(d.value)}
                  className={`w-full text-left p-3 rounded-lg border-2 transition-colors ${
                    isSelected ? "border-bioaf-500 bg-bioaf-50" : "border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm">{d.label}</span>
                    <span className="text-xs text-gray-500">{parts.length} segments</span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {parts.map((part, i) => (
                      <span key={i} className="bg-white border rounded px-2 py-0.5 font-mono text-xs">
                        {part}
                      </span>
                    ))}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={startWalk}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
            >
              Next
            </button>
            <button onClick={() => setPhase("pick-file")} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">
              Back
            </button>
          </div>
        </div>
      )}

      {/* Phase 3: Walk through segments one at a time */}
      {phase === "walk-segments" && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-lg font-semibold">
              What is &ldquo;<span className="font-mono text-bioaf-700">{segments[activeIndex]}</span>&rdquo;?
            </h2>
            <span className="text-xs text-gray-400">
              Segment {activeIndex + 1} of {segments.length}
            </span>
          </div>

          {renderSegmentHighlight()}

          {/* Role selection (if not yet picked for this segment) */}
          {!needsEntity && (
            <div className="grid grid-cols-2 gap-2">
              {SEGMENT_ROLES.map((role) => (
                <button
                  key={role.value}
                  onClick={() => assignRole(role.value)}
                  className="text-left p-3 rounded-lg border border-gray-200 hover:border-bioaf-400 hover:bg-bioaf-50 transition-colors"
                >
                  <span className="text-sm font-medium">{role.label}</span>
                </button>
              ))}
            </div>
          )}

          {/* Entity selection (after picking project/experiment/sample) */}
          {needsEntity && !currentMapping.entityId && (
            <div>
              <p className="text-sm text-gray-600 mb-3">
                Which {currentMapping.role === "project_code" ? "project" : currentMapping.role === "experiment_code" ? "experiment" : "sample"} does
                &ldquo;<span className="font-mono font-bold">{segments[activeIndex]}</span>&rdquo; represent?
              </p>
              {currentMapping.role === "project_code" && (
                <div className="space-y-1 max-h-60 overflow-y-auto">
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => assignEntity(p.id, p.name)}
                      className="w-full text-left px-3 py-2 rounded-lg border border-gray-200 hover:border-bioaf-400 hover:bg-bioaf-50 transition-colors text-sm"
                    >
                      <span className="font-medium">{p.name}</span>
                      {p.code && <span className="ml-2 text-gray-400 font-mono text-xs">{p.code}</span>}
                    </button>
                  ))}
                </div>
              )}
              {currentMapping.role === "experiment_code" && (
                <div className="space-y-1 max-h-60 overflow-y-auto">
                  {experiments.map((exp) => (
                    <button
                      key={exp.id}
                      onClick={() => assignEntity(exp.id, exp.name)}
                      className="w-full text-left px-3 py-2 rounded-lg border border-gray-200 hover:border-bioaf-400 hover:bg-bioaf-50 transition-colors text-sm"
                    >
                      <span className="font-medium">{exp.name}</span>
                      {exp.code && <span className="ml-2 text-gray-400 font-mono text-xs">{exp.code}</span>}
                    </button>
                  ))}
                </div>
              )}
              {currentMapping.role === "sample_id" && (
                <div>
                  <p className="text-xs text-gray-500 mb-2">
                    Sample IDs are matched automatically during ingest. No selection needed.
                  </p>
                  <button
                    onClick={advanceSegment}
                    className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 text-sm"
                  >
                    Continue
                  </button>
                </div>
              )}
              <button
                onClick={() => {
                  setMappings((prev) => prev.map((m, i) => (i === activeIndex ? { ...m, role: "" } : m)));
                }}
                className="mt-3 text-sm text-gray-500 hover:text-gray-700"
              >
                &larr; Pick a different role
              </button>
            </div>
          )}

          <div className="flex gap-2 mt-4 pt-4 border-t">
            {activeIndex > 0 && (
              <button onClick={goBackSegment} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 text-sm">
                Back
              </button>
            )}
            <button onClick={onCancel} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Phase 4: Name and save */}
      {phase === "name-and-save" && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Profile Summary</h2>

          <div className="flex flex-wrap items-center gap-0.5 font-mono text-sm bg-gray-50 rounded-lg p-4">
            {mappings.map((m, i) => (
              <span key={i} className="flex items-center">
                {i > 0 && <span className="text-gray-300 mx-0.5">{delimiter}</span>}
                <span className={`px-2 py-1 rounded border ${
                  m.role === "ignore" || !m.role ? "bg-gray-100 border-gray-200 text-gray-400" : "bg-green-50 border-green-300 text-green-800"
                }`}>
                  {m.value}
                  {m.role && m.role !== "ignore" && (
                    <span className="ml-1 text-xs font-sans">
                      ({SEGMENT_ROLES.find((r) => r.value === m.role)?.label}
                      {m.entityName && `: ${m.entityName}`})
                    </span>
                  )}
                </span>
              </span>
            ))}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Profile name</label>
            <input
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder={`Profile for ${stripExtension(filename)}`}
              className="w-full border rounded-lg px-3 py-2 text-sm"
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-md p-3">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save Profile"}
            </button>
            <button
              onClick={() => { setActiveIndex(0); setPhase("walk-segments"); }}
              className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
            >
              Re-do mapping
            </button>
            <button onClick={onCancel} className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

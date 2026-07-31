"use client";

import { useEffect, useState } from "react";
import {
  cleanText,
  formatAssociation,
  formatAuthors,
  formatYear,
  literature,
  type Paper,
} from "@/lib/literature";
import { statusBadgeClass } from "@/lib/statusStyles";

interface Props {
  experimentId?: number;
  projectId?: number;
}

const PROVENANCE_LABELS: Record<string, string> = {
  user_upload: "Uploaded",
  source_search: "From search",
  lit_review_run: "AI Lit Review",
};

export function LiteratureTabPanel({ experimentId, projectId }: Props) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const filters = experimentId
      ? {
          experiment_id: experimentId,
          include_parent_project: true,
          page_size: 100,
        }
      : projectId
        ? { project_id: projectId, page_size: 100 }
        : { page_size: 100 };

    literature
      .listPapers(filters)
      .then((data) => {
        if (cancelled) return;
        setPapers(data.items);
        setTotal(data.total);
      })
      .catch(() => {
        if (cancelled) return;
        setPapers([]);
        setTotal(0);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [experimentId, projectId]);

  if (loading) {
    return <div className="text-gray-500 text-sm">Loading literature...</div>;
  }

  if (papers.length === 0) {
    return (
      <div className="border border-dashed border-gray-300 rounded p-8 text-center text-gray-500 text-sm">
        No papers associated with this {experimentId ? "experiment" : "project"}{" "}
        yet. Add papers from the Library, or run AI Lit Review from the
        Recommendations page to surface candidates.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-gray-500">{total} papers</div>
      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Title
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-40">
                Authors
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-16">
                Year
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-32">
                Source
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Scope
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {papers.map((p) => (
              <tr key={p.id} className="align-top hover:bg-gray-50">
                <td className="px-4 py-3 text-sm">
                  <a
                    href={`/lab-knowledge/literature/papers/${p.id}`}
                    className="text-bioaf-700 hover:underline font-medium"
                  >
                    {cleanText(p.title)}
                  </a>
                  {p.journal && (
                    <div className="text-xs text-gray-500 mt-0.5">
                      {cleanText(p.journal)}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-sm text-gray-600">
                  {formatAuthors(p.authors)}
                </td>
                <td className="px-4 py-3 text-sm text-gray-600">
                  {formatYear(p.publication_date)}
                </td>
                <td className="px-4 py-3 text-xs">
                  <span
                    className={`inline-block px-2 py-0.5 rounded ${
                      statusBadgeClass("literatureProvenance", p.provenance)
                    }`}
                  >
                    {PROVENANCE_LABELS[p.provenance] ?? p.provenance}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs">
                  <div className="flex flex-wrap gap-1">
                    {p.associations.map((a) => (
                      <span
                        key={a.id}
                        className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-800"
                      >
                        {formatAssociation(a)}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

"use client";

import { useToast } from "@/components/shared/Toast";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ReviewBadge } from "./ReviewBadge";
import type {
  PipelineRunReview,
  PipelineRunReviewListResponse,
  ReviewVerdict,
} from "@/lib/types";

interface ReviewPanelProps {
  pipelineRunId: number;
  userRole: string;
  onReviewSubmitted?: () => void;
}

export function ReviewPanel({ pipelineRunId, userRole, onReviewSubmitted }: ReviewPanelProps) {
  const toast = useToast();
  const [reviews, setReviews] = useState<PipelineRunReview[]>([]);
  const [activeReview, setActiveReview] = useState<PipelineRunReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [verdict, setVerdict] = useState<ReviewVerdict>("approved");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const canReview = userRole === "admin" || userRole === "comp_bio";

  useEffect(() => {
    loadReviews();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineRunId]);

  async function loadReviews() {
    try {
      const [reviewList, active] = await Promise.all([
        api.get<PipelineRunReviewListResponse>(`/api/pipeline-runs/${pipelineRunId}/reviews`),
        api.get<PipelineRunReview>(`/api/pipeline-runs/${pipelineRunId}/review`).catch(() => null),
      ]);
      setReviews(reviewList.reviews);
      setActiveReview(active);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmitReview() {
    setSubmitting(true);
    try {
      await api.post(`/api/pipeline-runs/${pipelineRunId}/reviews`, {
        verdict,
        notes: notes || null,
      });
      setShowForm(false);
      setNotes("");
      loadReviews();
      onReviewSubmitted?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to submit review");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-gray-500">Loading reviews...</p>;
  }

  return (
    <div className="space-y-4">
      {activeReview && (
        <div className="bg-white rounded-lg shadow p-4 border-l-4 border-green-500">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-sm">Active Review</h3>
            <ReviewBadge verdict={activeReview.verdict} size="md" />
          </div>
          <p className="text-sm text-gray-600">
            Reviewed by {activeReview.reviewer.name || activeReview.reviewer.email} on{" "}
            {new Date(activeReview.reviewed_at).toLocaleString()}
          </p>
          {activeReview.notes && (
            <p className="text-sm text-gray-500 mt-2 bg-gray-50 p-2 rounded">{activeReview.notes}</p>
          )}
          {activeReview.recommended_exclusions && activeReview.recommended_exclusions.length > 0 && (
            <p className="text-xs text-gray-500 mt-2">
              Recommended exclusions: sample IDs {activeReview.recommended_exclusions.join(", ")}
            </p>
          )}
        </div>
      )}

      {canReview && (
        <div>
          <button
            onClick={() => setShowForm(!showForm)}
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
          >
            {activeReview ? "Submit New Review" : "Submit Review"}
          </button>
        </div>
      )}

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3">Pipeline Run Review</h3>
          <div className="space-y-3">
            <div>
              <label htmlFor="review-verdict" className="block text-xs text-gray-500 mb-1">Verdict</label>
              <select
                id="review-verdict"
                value={verdict}
                onChange={(e) => setVerdict(e.target.value as ReviewVerdict)}
                className="border rounded px-3 py-2 text-sm w-full"
              >
                <option value="approved">Approved</option>
                <option value="approved_with_caveats">Approved with Caveats</option>
                <option value="rejected">Rejected</option>
                <option value="revision_requested">Revision Requested</option>
              </select>
            </div>
            <div>
              <label htmlFor="review-notes" className="block text-xs text-gray-500 mb-1">Notes</label>
              <textarea
                id="review-notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Review notes, observations, caveats..."
                className="border rounded px-3 py-2 text-sm w-full h-24 resize-y"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSubmitReview}
                disabled={submitting}
                className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50"
              >
                {submitting ? "Submitting..." : "Submit Review"}
              </button>
              <button onClick={() => setShowForm(false)} className="border px-4 py-1.5 rounded text-sm">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {reviews.length > 1 && (
        <div>
          <h4 className="text-sm font-medium text-gray-600 mb-2">Review History</h4>
          <div className="space-y-2">
            {reviews
              .filter((r) => !r.is_active)
              .map((r) => (
                <div key={r.id} className="bg-gray-50 rounded p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">
                      {r.reviewer.name || r.reviewer.email}: {new Date(r.reviewed_at).toLocaleString()}
                    </span>
                    <ReviewBadge verdict={r.verdict} />
                  </div>
                  {r.notes && <p className="text-gray-500 text-xs mt-1">{r.notes}</p>}
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

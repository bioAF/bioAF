"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { getCurrentUser, isAuthenticated } from "@/lib/auth";
import {
  cleanText,
  literature,
  type Comment,
  type Paper,
  type ReadingStatusValue,
  type RecommendationNote,
  formatAuthors,
  formatYear,
} from "@/lib/literature";

export default function PaperDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const user = getCurrentUser();
  const paperId = Number(params.id);

  const canComment =
    user?.role_name === "admin" ||
    user?.role_name === "comp_bio" ||
    user?.role_name === "bench";
  const canDismiss =
    user?.role_name === "admin" || user?.role_name === "comp_bio";
  const canReverseDismiss = user?.role_name === "admin";

  const [paper, setPaper] = useState<Paper | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [notes, setNotes] = useState<RecommendationNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [commentBody, setCommentBody] = useState("");
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [replyBody, setReplyBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [addingToLibrary, setAddingToLibrary] = useState(false);

  function refresh() {
    setLoading(true);
    Promise.all([
      literature.getPaper(paperId),
      literature.listComments(paperId),
      literature.recommendationNotes(paperId).catch(() => []),
    ])
      .then(([p, c, n]) => {
        setPaper(p);
        setComments(c.items);
        setNotes(n);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }

  async function addToLibrary() {
    setAddingToLibrary(true);
    try {
      await literature.addToLibrary(paperId);
      refresh();
    } finally {
      setAddingToLibrary(false);
    }
  }

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  async function submitComment() {
    if (!commentBody.trim()) return;
    await literature.addComment(paperId, { body: commentBody });
    setCommentBody("");
    refresh();
  }
  async function submitReply(parentId: number) {
    if (!replyBody.trim()) return;
    await literature.addComment(paperId, { body: replyBody, parent_id: parentId });
    setReplyBody("");
    setReplyToId(null);
    refresh();
  }
  async function deleteComment(commentId: number) {
    if (!confirm("Delete this comment?")) return;
    await literature.deleteComment(commentId);
    refresh();
  }
  async function setReading(status: ReadingStatusValue) {
    await literature.setReadingStatus(paperId, status);
    refresh();
  }
  async function dismiss() {
    const reason = prompt(
      "Why dismiss this paper? (reason is logged and shown on the dismissed card)",
    );
    await literature.dismissPaper(paperId, reason ?? undefined);
    refresh();
  }
  async function reverseDismiss() {
    if (!confirm("Reverse the dismissal?")) return;
    await literature.reverseDismiss(paperId);
    refresh();
  }
  async function downloadCitation(format: "bibtex" | "ris") {
    const text = await literature.citation(paperId, format);
    await navigator.clipboard.writeText(text);
    alert(`${format.toUpperCase()} citation copied to clipboard.`);
  }

  if (loading) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col">
          <Header />
          <LoadingSpinner />
        </div>
      </div>
    );
  }
  if (!paper) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col">
          <Header />
          <main className="p-6">
            <div className="text-gray-600">Paper not found.</div>
            {error && <div className="text-red-700">{error}</div>}
          </main>
        </div>
      </div>
    );
  }

  const topLevel = comments.filter((c) => c.parent_id === null);
  const repliesByParent = new Map<number, Comment[]>();
  for (const c of comments) {
    if (c.parent_id !== null) {
      const list = repliesByParent.get(c.parent_id) ?? [];
      list.push(c);
      repliesByParent.set(c.parent_id, list);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => router.push("/data/literature")}
            className="text-bioaf-700 hover:underline text-sm mb-4"
          >
            ← Back to library
          </button>
          <h1 className="text-2xl font-bold mb-2">{cleanText(paper.title)}</h1>
          <div className="text-gray-600 mb-4">
            {formatAuthors(paper.authors)} · {formatYear(paper.publication_date)} ·{" "}
            {cleanText(paper.journal)}
          </div>
          {paper.dismissed && (
            <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-2 rounded mb-4">
              This paper is dismissed org-wide and excluded from Agent Review.
            </div>
          )}
          {!paper.in_library && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 px-4 py-2 rounded mb-4 flex items-center justify-between">
              <span>
                This paper appears in your search history but has not been added
                to the Library. Add it to track comments, reading status, and
                associations.
              </span>
              {canComment && (
                <button
                  onClick={addToLibrary}
                  disabled={addingToLibrary}
                  className="ml-4 bg-amber-600 text-white px-3 py-1.5 rounded text-sm hover:bg-amber-700 disabled:opacity-50"
                >
                  {addingToLibrary ? "Adding..." : "Add to Library"}
                </button>
              )}
            </div>
          )}

          {notes.length > 0 && (
            <div className="bg-purple-50 border border-purple-200 rounded mb-4 p-4">
              <h2 className="font-semibold text-purple-900 mb-2 flex items-center gap-2">
                <span className="inline-block w-6 h-6 rounded-full bg-purple-600 text-white text-xs flex items-center justify-center">
                  AI
                </span>
                Lit Review Bot notes
              </h2>
              <ul className="space-y-3">
                {notes.map((n, i) => (
                  <li key={i} className="text-sm">
                    <div className="text-xs text-purple-700 mb-1">
                      Run #{n.review_run_id} for experiment #{n.experiment_id} &middot;{" "}
                      relevance {n.relevance_score.toFixed(2)} ({n.relevance_bucket})
                      {" · "}
                      {n.llm_provider}
                      {n.llm_model ? `/${n.llm_model}` : ""}
                      {" · "}
                      {new Date(n.created_at).toLocaleString()}
                    </div>
                    <div className="text-purple-900 whitespace-pre-wrap">
                      {n.reasoning ?? "(no reasoning recorded)"}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid md:grid-cols-3 gap-6">
            <section className="md:col-span-2 space-y-6">
              {paper.abstract && (
                <div className="bg-white rounded shadow p-4">
                  <h2 className="font-semibold mb-2">Abstract</h2>
                  <p className="text-sm whitespace-pre-wrap">
                    {cleanText(paper.abstract)}
                  </p>
                </div>
              )}
              <div className="bg-white rounded shadow p-4">
                <h2 className="font-semibold mb-2">Comments ({paper.comment_count})</h2>
                {canComment && (
                  <div className="mb-4">
                    <textarea
                      value={commentBody}
                      onChange={(e) => setCommentBody(e.target.value)}
                      rows={3}
                      placeholder="Add a comment"
                      className="w-full border border-gray-300 rounded px-3 py-2"
                    />
                    <div className="flex justify-end mt-1">
                      <button
                        onClick={submitComment}
                        disabled={!commentBody.trim()}
                        className="bg-bioaf-600 text-white px-3 py-1 rounded text-sm disabled:opacity-50"
                      >
                        Post
                      </button>
                    </div>
                  </div>
                )}
                {topLevel.length === 0 ? (
                  <div className="text-sm text-gray-500">No comments yet.</div>
                ) : (
                  <ul className="space-y-3">
                    {topLevel.map((c) => (
                      <li key={c.id} className="border-l-2 border-gray-200 pl-3">
                        <CommentCard
                          c={c}
                          canDelete={canComment && (c.user_id === user?.id || canDismiss)}
                          onReplyClick={() => setReplyToId(c.id)}
                          onDelete={() => deleteComment(c.id)}
                          canComment={canComment}
                        />
                        {replyToId === c.id && (
                          <div className="ml-4 mt-2">
                            <textarea
                              value={replyBody}
                              onChange={(e) => setReplyBody(e.target.value)}
                              rows={2}
                              placeholder="Reply..."
                              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                            />
                            <div className="flex gap-2 mt-1 justify-end">
                              <button
                                onClick={() => setReplyToId(null)}
                                className="px-3 py-1 text-sm border border-gray-300 rounded"
                              >
                                Cancel
                              </button>
                              <button
                                onClick={() => submitReply(c.id)}
                                disabled={!replyBody.trim()}
                                className="bg-bioaf-600 text-white px-3 py-1 rounded text-sm disabled:opacity-50"
                              >
                                Reply
                              </button>
                            </div>
                          </div>
                        )}
                        <ul className="ml-4 mt-2 space-y-2">
                          {(repliesByParent.get(c.id) ?? []).map((r) => (
                            <li key={r.id} className="border-l-2 border-gray-100 pl-3">
                              <CommentCard
                                c={r}
                                canDelete={canComment && (r.user_id === user?.id || canDismiss)}
                                onReplyClick={() => setReplyToId(c.id)}
                                onDelete={() => deleteComment(r.id)}
                                canComment={canComment}
                              />
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>

            <aside className="space-y-4">
              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Metadata</h3>
                <dl className="text-sm space-y-1">
                  <dt className="text-gray-500">DOI</dt>
                  <dd>
                    {paper.doi ? (
                      <a
                        href={`https://doi.org/${paper.doi}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-bioaf-700 hover:underline break-all"
                      >
                        {paper.doi}
                      </a>
                    ) : (
                      "—"
                    )}
                  </dd>
                  <dt className="text-gray-500 mt-2">PMID</dt>
                  <dd>{paper.pmid ?? "—"}</dd>
                  <dt className="text-gray-500 mt-2">Provenance</dt>
                  <dd>{paper.provenance}</dd>
                  <dt className="text-gray-500 mt-2">Extraction</dt>
                  <dd>{paper.extraction_status}</dd>
                </dl>
              </div>

              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Reading status</h3>
                <div className="flex gap-2">
                  {(["unread", "reading", "read"] as ReadingStatusValue[]).map(
                    (status) => (
                      <button
                        key={status}
                        onClick={() => setReading(status)}
                        className={
                          paper.reading_status === status
                            ? "bg-bioaf-600 text-white px-3 py-1 rounded text-sm"
                            : "border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50"
                        }
                      >
                        {status}
                      </button>
                    ),
                  )}
                </div>
              </div>

              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Associations</h3>
                {paper.associations.length === 0 ? (
                  <div className="text-sm text-gray-500">
                    No associations. Manage from the Library to link this paper
                    to a project or experiment.
                  </div>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {paper.associations.map((a) => (
                      <li
                        key={a.id}
                        className="flex justify-between items-center"
                      >
                        {a.scope_type === "global" || a.scope_id === null ? (
                          <span>Global</span>
                        ) : (
                          <a
                            href={
                              a.scope_type === "project"
                                ? `/projects/${a.scope_id}`
                                : `/experiments/${a.scope_id}`
                            }
                            className="text-bioaf-700 hover:underline"
                          >
                            {a.scope_name ?? `#${a.scope_id}`}
                          </a>
                        )}
                        {(user?.id === a.added_by_user_id ||
                          canDismiss ||
                          user?.role_name === "admin") && (
                          <button
                            onClick={async () => {
                              if (!confirm("Remove this association?")) return;
                              await literature.deleteAssociation(paper.id, a.id);
                              refresh();
                            }}
                            className="border border-red-300 text-red-700 px-2 py-0.5 rounded text-xs hover:bg-red-50"
                          >
                            Remove
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Citation</h3>
                <div className="flex gap-2">
                  <button
                    onClick={() => downloadCitation("bibtex")}
                    className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50"
                  >
                    Copy BibTeX
                  </button>
                  <button
                    onClick={() => downloadCitation("ris")}
                    className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50"
                  >
                    Copy RIS
                  </button>
                </div>
              </div>

              {(canDismiss || canReverseDismiss) && (
                <div className="bg-white rounded shadow p-4">
                  <h3 className="font-semibold mb-2">Admin</h3>
                  {!paper.dismissed && canDismiss && (
                    <button
                      onClick={dismiss}
                      className="border border-red-300 text-red-700 px-3 py-1 rounded text-sm hover:bg-red-50"
                    >
                      Dismiss org-wide
                    </button>
                  )}
                  {paper.dismissed && canReverseDismiss && (
                    <button
                      onClick={reverseDismiss}
                      className="border border-bioaf-300 text-bioaf-700 px-3 py-1 rounded text-sm hover:bg-bioaf-50"
                    >
                      Reverse dismissal
                    </button>
                  )}
                </div>
              )}
            </aside>
          </div>
        </main>
      </div>
    </div>
  );
}

function CommentCard({
  c,
  canDelete,
  onReplyClick,
  onDelete,
  canComment,
}: {
  c: Comment;
  canDelete: boolean;
  onReplyClick: () => void;
  onDelete: () => void;
  canComment: boolean;
}) {
  return (
    <div className="text-sm">
      <div className="flex justify-between items-center mb-1">
        <span className="text-gray-500 text-xs">
          {new Date(c.created_at).toLocaleString()}
        </span>
        <div className="flex gap-2">
          {canComment && !c.deleted && (
            <button
              onClick={onReplyClick}
              className="text-bioaf-700 text-xs hover:underline"
            >
              reply
            </button>
          )}
          {canDelete && !c.deleted && (
            <button
              onClick={onDelete}
              className="text-red-700 text-xs hover:underline"
            >
              delete
            </button>
          )}
        </div>
      </div>
      {c.deleted ? (
        <em className="text-gray-500">[deleted]</em>
      ) : (
        <span className="whitespace-pre-wrap">{c.body}</span>
      )}
    </div>
  );
}

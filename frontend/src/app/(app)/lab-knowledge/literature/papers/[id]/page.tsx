"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { InputDialog } from "@/components/shared/InputDialog";
import { getCurrentUser } from "@/lib/auth";
import { PaperPdfViewer } from "@/components/literature/PaperPdfViewer";
import { AssociatePaperModal } from "@/components/literature/AssociatePaperModal";
import { ValidatePaperButton } from "@/components/validation/ValidatePaperButton";
import {
  advanceReadingStatus,
  cleanText,
  DoiConflictError,
  formatAssociation,
  literature,
  uploadPdfToPaper,
  type Comment,
  type DoiConflict,
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
  const canAssociate =
    user?.role_name === "admin" ||
    user?.role_name === "comp_bio" ||
    user?.role_name === "bench";
  const canDismiss =
    user?.role_name === "admin" || user?.role_name === "comp_bio";
  const canReverseDismiss = user?.role_name === "admin";
  const canDeletePaper =
    user?.role_name === "admin" || user?.role_name === "comp_bio";

  const [paper, setPaper] = useState<Paper | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [notes, setNotes] = useState<RecommendationNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [commentBody, setCommentBody] = useState("");
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [replyBody, setReplyBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [addingToLibrary, setAddingToLibrary] = useState(false);
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pendingPdf, setPendingPdf] = useState<File | null>(null);
  const [conflict, setConflict] = useState<DoiConflict | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [associating, setAssociating] = useState(false);
  // One generic confirm dialog drives the small yes/no actions (delete comment,
  // reverse dismissal, remove association) instead of a native confirm().
  const [pendingConfirm, setPendingConfirm] = useState<{
    title: string;
    message: string;
    confirmLabel: string;
    variant?: "danger" | "default";
    run: () => Promise<void>;
  } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [dismissBusy, setDismissBusy] = useState(false);
  const [copiedCitation, setCopiedCitation] = useState<string | null>(null);

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

  async function doUpload(file: File, confirmMerge: boolean) {
    setUploadingPdf(true);
    setPdfError(null);
    try {
      await uploadPdfToPaper(paperId, file, confirmMerge);
      setPendingPdf(null);
      setConflict(null);
      refresh();
    } catch (e) {
      if (e instanceof DoiConflictError) {
        setPendingPdf(file);
        setConflict(e.conflict);
      } else {
        setPdfError(e instanceof Error ? e.message : "Upload failed.");
      }
    } finally {
      setUploadingPdf(false);
    }
  }

  function onPdfSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setPdfError("Only PDF files are supported.");
      return;
    }
    doUpload(file, false);
  }

  useEffect(() => {
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
  function deleteComment(commentId: number) {
    setPendingConfirm({
      title: "Delete comment",
      message: "Delete this comment? This cannot be undone.",
      confirmLabel: "Delete",
      variant: "danger",
      run: async () => {
        await literature.deleteComment(commentId);
        refresh();
      },
    });
  }
  async function setReading(status: ReadingStatusValue) {
    await literature.setReadingStatus(paperId, status);
    refresh();
  }
  // Advance reading status from how far the reader has scrolled: page 2 implies
  // Reading, the last page implies Read. Forward-only, so it never undoes a
  // manual status. Updates locally and persists in the background.
  async function handleReachPage(page: number, totalPages: number) {
    if (!paper) return;
    const next = advanceReadingStatus(paper.reading_status, page, totalPages);
    if (!next) return;
    setPaper({ ...paper, reading_status: next });
    try {
      await literature.setReadingStatus(paperId, next);
    } catch {
      // Best-effort: the manual reading-status buttons remain available.
    }
  }
  async function deletePaper() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await literature.deletePaper(paperId);
      router.push("/lab-knowledge/literature");
    } catch (e) {
      // Keep the dialog open so the inline error is visible.
      setDeleting(false);
      setDeleteError(e instanceof Error ? e.message : "Delete failed.");
    }
  }
  function dismiss() {
    setDismissOpen(true);
  }
  async function submitDismiss(reason: string) {
    setDismissBusy(true);
    try {
      await literature.dismissPaper(paperId, reason.trim() ? reason.trim() : undefined);
      setDismissOpen(false);
      refresh();
    } finally {
      setDismissBusy(false);
    }
  }
  function reverseDismiss() {
    setPendingConfirm({
      title: "Reverse dismissal",
      message: "Reverse the dismissal? The paper returns to the active Library.",
      confirmLabel: "Reverse",
      run: async () => {
        await literature.reverseDismiss(paperId);
        refresh();
      },
    });
  }
  function removeAssociation(associationId: number) {
    setPendingConfirm({
      title: "Remove association",
      message: "Remove this association? The link between this paper and that scope is deleted.",
      confirmLabel: "Remove",
      variant: "danger",
      run: async () => {
        await literature.deleteAssociation(paperId, associationId);
        refresh();
      },
    });
  }
  async function runPendingConfirm() {
    if (!pendingConfirm) return;
    setConfirmBusy(true);
    try {
      await pendingConfirm.run();
      setPendingConfirm(null);
    } finally {
      setConfirmBusy(false);
    }
  }
  async function downloadCitation(format: "bibtex" | "ris") {
    const text = await literature.citation(paperId, format);
    await navigator.clipboard.writeText(text);
    setCopiedCitation(format === "bibtex" ? "BibTeX" : "RIS");
    window.setTimeout(() => setCopiedCitation(null), 2500);
  }

  if (loading) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LoadingSpinner />
      </main>
    );
  }
  if (!paper) {
    return (
      <main className="p-6">
        <div className="text-gray-600">Paper not found.</div>
        {error && <div className="text-red-700">{error}</div>}
      </main>
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
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <button
          onClick={() => router.push("/lab-knowledge/literature")}
          className="text-bioaf-700 hover:underline text-sm mb-4"
        >
          ← Back to library
        </button>
        <h1 className="text-2xl font-bold mb-2">{cleanText(paper.title)}</h1>
        <div className="text-gray-600 mb-4">
          {formatAuthors(paper.authors)} · {formatYear(paper.publication_date)} ·{" "}
          {cleanText(paper.journal)}
        </div>
        <div className="mb-4">
          <ValidatePaperButton paperId={paper.id} doi={paper.doi} />
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
              {notes.map((n, i) => {
                const expLabel = n.experiment_name
                  ? n.project_name
                    ? `${n.project_name} > ${n.experiment_name}`
                    : n.experiment_name
                  : `experiment #${n.experiment_id}`;
                return (
                  <li key={i} className="text-sm">
                    <div className="text-xs text-purple-700 mb-1">
                      Recommended for{" "}
                      <a
                        href={`/experiments/${n.experiment_id}`}
                        className="font-medium hover:underline"
                      >
                        {expLabel}
                      </a>
                      {" · "}
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
                );
              })}
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
            {paper.has_pdf && (
              <div className="bg-white rounded shadow p-4">
                <h2 className="font-semibold mb-2">Paper PDF</h2>
                <PaperPdfViewer
                  paperId={paper.id}
                  filename={`${cleanText(paper.title).slice(0, 80) || "paper"}.pdf`}
                  onReachPage={handleReachPage}
                />
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
            {canComment && (
              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Full paper</h3>
                <p className="text-sm text-gray-600 mb-2">
                  {paper.has_pdf
                    ? "A PDF is attached. Upload a new file to replace it."
                    : "No PDF attached yet. Upload the full paper to replace this abstract-only entry."}
                </p>
                <label className="inline-block">
                  <span
                    className={`inline-block px-3 py-1.5 rounded text-sm cursor-pointer ${
                      uploadingPdf
                        ? "bg-gray-200 text-gray-500"
                        : "bg-bioaf-600 text-white hover:bg-bioaf-700"
                    }`}
                  >
                    {uploadingPdf
                      ? "Uploading..."
                      : paper.has_pdf
                        ? "Replace PDF"
                        : "Upload full paper"}
                  </span>
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    className="hidden"
                    disabled={uploadingPdf}
                    onChange={onPdfSelected}
                  />
                </label>
                {paper.has_pdf && (
                  <p className="text-xs text-gray-500 mt-2">
                    The current PDF is shown in the viewer on the left.
                  </p>
                )}
                {pdfError && (
                  <div className="text-xs text-red-700 mt-2">{pdfError}</div>
                )}
              </div>
            )}

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
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold">Associations</h3>
                {canAssociate && (
                  <button
                    onClick={() => setAssociating(true)}
                    className="text-bioaf-700 hover:underline text-sm"
                  >
                    + Associate
                  </button>
                )}
              </div>
              {paper.associations.length === 0 ? (
                <div className="text-sm text-gray-500">
                  No associations yet.
                  {canAssociate
                    ? " Use Associate to link this paper to a project or experiment."
                    : ""}
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
                          {formatAssociation(a)}
                        </a>
                      )}
                      {(user?.id === a.added_by_user_id ||
                        canDismiss ||
                        user?.role_name === "admin") && (
                        <button
                          onClick={() => removeAssociation(a.id)}
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
              {copiedCitation && (
                <p className="mt-2 text-sm text-green-700" role="status">
                  {copiedCitation} citation copied to clipboard.
                </p>
              )}
            </div>

            {(canDismiss || canReverseDismiss || canDeletePaper) && (
              <div className="bg-white rounded shadow p-4">
                <h3 className="font-semibold mb-2">Admin</h3>
                <div className="flex flex-col items-start gap-2">
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
                  {canDeletePaper && (
                    <button
                      onClick={() => {
                        setDeleteError(null);
                        setConfirmingDelete(true);
                      }}
                      className="border border-red-300 text-red-700 px-3 py-1 rounded text-sm hover:bg-red-50"
                    >
                      Delete paper
                    </button>
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>
      </main>

      <AssociatePaperModal
        paperIds={associating ? [paper.id] : []}
        onClose={() => setAssociating(false)}
        onAssociated={refresh}
      />

      <ConfirmDialog
        open={pendingConfirm !== null}
        title={pendingConfirm?.title ?? ""}
        message={pendingConfirm?.message ?? ""}
        confirmLabel={pendingConfirm?.confirmLabel ?? "Confirm"}
        variant={pendingConfirm?.variant ?? "default"}
        busy={confirmBusy}
        onConfirm={runPendingConfirm}
        onCancel={() => setPendingConfirm(null)}
      />

      <InputDialog
        open={dismissOpen}
        title="Dismiss paper org-wide"
        message="The reason is logged and shown on the dismissed card. Leave it blank to dismiss without a note."
        label="Reason (optional)"
        placeholder="e.g. superseded by a newer study"
        multiline
        allowEmpty
        confirmLabel="Dismiss"
        busyLabel="Dismissing..."
        busy={dismissBusy}
        onConfirm={submitDismiss}
        onCancel={() => setDismissOpen(false)}
      />

      {confirmingDelete && (
        <div className="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 w-[28rem]">
            <h3 className="font-semibold mb-2">Delete this paper?</h3>
            <p className="text-sm text-gray-600 mb-4">
              This deletes the uploaded PDF and any stored files from cloud
              storage, and dismisses the paper org-wide. It leaves your active
              Library and is excluded from AI Literature Review. The abstract,
              metadata, comments, and history are kept; an admin can reverse the
              dismissal, but the deleted PDF would need to be uploaded again.
            </p>
            {deleteError && (
              <p className="text-sm text-red-700 mb-4">{deleteError}</p>
            )}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setDeleteError(null);
                  setConfirmingDelete(false);
                }}
                disabled={deleting}
                className="px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={deletePaper}
                disabled={deleting}
                className="px-3 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Delete paper"}
              </button>
            </div>
          </div>
        </div>
      )}

      {conflict && pendingPdf && (
        <div className="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 w-[28rem]">
            <h3 className="font-semibold mb-2">A paper with this DOI exists</h3>
            <p className="text-sm text-gray-600 mb-4">
              Another library entry already uses DOI{" "}
              <span className="font-mono">{conflict.doi}</span>:{" "}
              <span className="font-medium">{conflict.other_paper_title}</span>.
              Replacing will merge that entry&apos;s comments, AI Lit Review
              notes, and associations into this paper, attach the uploaded PDF
              here, and delete the duplicate. This cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setConflict(null);
                  setPendingPdf(null);
                }}
                disabled={uploadingPdf}
                className="px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={() => doUpload(pendingPdf, true)}
                disabled={uploadingPdf}
                className="px-3 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:opacity-50"
              >
                {uploadingPdf ? "Merging..." : "Replace and merge"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
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
        <div className="text-xs text-gray-500">
          <span className="font-medium text-gray-700">
            {c.user_name ?? `User #${c.user_id}`}
          </span>
          <span className="ml-2">{new Date(c.created_at).toLocaleString()}</span>
        </div>
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

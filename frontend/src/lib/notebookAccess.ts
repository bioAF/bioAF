/**
 * Opening a notebook session through the protected gateway (plan 03.5A).
 *
 * A protected session's address is its gateway path ("/s/{id}/"). Open asks bioAF for a single-use,
 * 60-second ticket and posts it in a form to the gateway's own origin, so the ticket never appears
 * in a URL, the history or a Referer. The tab is opened during the click so it is not blocked as a
 * popup; the gateway's pages cut the opener link (Cross-Origin-Opener-Policy).
 */
import { api } from "@/lib/api";

interface SessionAccess {
  entry_url: string;
  ticket: string;
  expires_in: number;
}

export function isProtectedAccess(url: string | null | undefined): boolean {
  return !!url && url.startsWith("/s/");
}

export async function openProtectedSession(sessionId: number): Promise<void> {
  const target = `bioaf-notebook-${sessionId}`;
  const tab = window.open("about:blank", target);
  try {
    const access = await api.post<SessionAccess>(`/api/v1/notebooks/sessions/${sessionId}/access`);
    const form = document.createElement("form");
    form.method = "POST";
    form.action = access.entry_url;
    form.target = target;
    form.style.display = "none";
    const ticket = document.createElement("input");
    ticket.type = "hidden";
    ticket.name = "ticket";
    ticket.value = access.ticket;
    form.appendChild(ticket);
    document.body.appendChild(form);
    try {
      form.submit();
    } finally {
      form.remove();
    }
  } catch (error) {
    tab?.close();
    throw error;
  }
}

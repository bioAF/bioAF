import { useSyncExternalStore } from "react";

/**
 * Shared open/closed state for the assistant, decoupling the LAUNCHER (which lives in the Header,
 * and so re-mounts on every navigation) from the PANEL (which stays mounted once in the root layout
 * so the conversation survives navigation). They can't share React state across that tree split, so
 * this tiny module-level store bridges them: the header launcher toggles it, the root-mounted panel
 * subscribes to it. Client-only singleton; the server snapshot is always "closed".
 */
type Listener = () => void;

let open = false;
const listeners = new Set<Listener>();

function emit() {
  for (const listener of listeners) listener();
}

export const assistantUiStore = {
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): boolean {
    return open;
  },
  open(): void {
    if (!open) {
      open = true;
      emit();
    }
  },
  close(): void {
    if (open) {
      open = false;
      emit();
    }
  },
  toggle(): void {
    open = !open;
    emit();
  },
};

export function useAssistantOpen(): boolean {
  return useSyncExternalStore(
    assistantUiStore.subscribe,
    assistantUiStore.getSnapshot,
    () => false,
  );
}

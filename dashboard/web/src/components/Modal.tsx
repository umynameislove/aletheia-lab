import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { X } from "lucide-react";

const FOCUSABLE = 'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/* Focus moves into the dialog, stays inside it while Tab cycles, and returns to where it came
   from; Esc and the scrim close it. While `busy`, nothing closes it: the action is still running. */
export function Modal({ title, onClose, busy = false, children }: { title: string; onClose: () => void; busy?: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button")?.focus();
    // what opened it may be gone by now (a deleted project's row): then focus goes to the screen, not the body
    return () => { if (prev?.isConnected) prev.focus(); else document.getElementById("main")?.focus(); };
  }, []);
  const close = () => { if (!busy) onClose(); };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); return; }
    if (e.key !== "Tab" || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  return (
    <div className="scrim" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={close} aria-label="Close" aria-disabled={busy || undefined}><X size={16} /></button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/* Asks before something that cannot be brought back. Keeping things is the safe default: the close
   button takes focus first, then Cancel, and the action comes last. */
export function ConfirmDialog({ title, action, onConfirm, onCancel, children }: {
  title: string; action: string; onConfirm: () => void; onCancel: () => void; children: ReactNode;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="notice notice--stop" style={{ marginTop: 0 }}><div>{children}</div></div>
      <div className="modal-actions">
        <button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn btn--danger" onClick={onConfirm}>{action}</button>
      </div>
    </Modal>
  );
}

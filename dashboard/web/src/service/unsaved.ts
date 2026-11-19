/* Review marks live only in this tab, so the browser is asked to warn before the tab is reloaded or
   closed while there are any. The caller adds this only while there is something to lose and removes
   it as soon as there is not (a listener left on would also warn for nothing). Moving between screens
   is a hash change, which never fires beforeunload, so the marks survive it without a warning. */

type Target = Pick<EventTarget, "addEventListener" | "removeEventListener">;

/** Starts warning on `target` (the window); the returned function stops. */
export function warnBeforeUnload(target: Target): () => void {
  // preventDefault is the standard; returnValue is what older Safari and Chrome still look for
  const warn = (e: Event) => { e.preventDefault(); (e as BeforeUnloadEvent).returnValue = ""; };
  target.addEventListener("beforeunload", warn);
  return () => target.removeEventListener("beforeunload", warn);
}

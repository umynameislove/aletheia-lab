/* Everything the client holds for one person's scope registers how to forget it, so ending a session
   cannot miss a draft in some lazily loaded screen. Display preferences are not scope and are not here. */

const wipes = new Set<() => void>();

export function onWipe(forget: () => void) {
  wipes.add(forget);
  return () => { wipes.delete(forget); };
}

export function wipeAll() {
  wipes.forEach(forget => forget());
}

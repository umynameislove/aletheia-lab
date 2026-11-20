/* Bounded lists (Q-08.03): the DOM holds one page, and every row stays reachable. A page number in
   the URL is clamped to what exists, so a stale link cannot show an empty list that looks like "no claims". */

export const PAGE_SIZE = 50;

export interface Page<T> { rows: T[]; page: number; pages: number; first: number; last: number; total: number }

export function pageOf<T>(all: readonly T[], requested: number, size = PAGE_SIZE): Page<T> {
  const total = all.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const page = Math.min(pages, Math.max(1, Number.isFinite(requested) ? Math.floor(requested) : 1));
  const start = (page - 1) * size;
  const rows = all.slice(start, start + size);
  return { rows, page, pages, first: total ? start + 1 : 0, last: start + rows.length, total };
}

/** The page holding the row at this index, so keyboard moves and deep links land on a page that shows it. */
export const pageOfIndex = (index: number, size = PAGE_SIZE) => Math.floor(Math.max(0, index) / size) + 1;

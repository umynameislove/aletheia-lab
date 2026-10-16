import raw from "./synthetic_p6_view.json";
import type { ProductView } from "../types";
export * from "./projection";

/** The shared fixture. SHA-256 0f541342…d957fd2 (Kiên, commit 4e37e551). */
export const VIEW = raw as ProductView;

import * as c from "./view_common.mjs";

// Drawn from the whole module, where the tool draws only the shortest run to each broken rule.
export const title = "Where a run breaks a rule";
export const state = (v) => (v.published ? `${c.where(v)}, though a role published` : c.where(v));
export const step = c.named;

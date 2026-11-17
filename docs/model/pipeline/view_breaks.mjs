import * as c from "./view_common.mjs";

export const title = "Where a run breaks a rule";
export const state = (v) => (v.published ? `${c.where(v)}, though a role published` : c.where(v));
export const step = c.named;

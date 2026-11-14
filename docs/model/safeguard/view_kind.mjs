import * as c from "./view_common.mjs";
export const title = "The kind's safeguard: what each green change does to it";
export const state = c.kind;
export const step = (_action, from, to) => c.change(from, to);

import * as c from "./view_common.mjs";

export const title = "One ticket's life, from buildable to merged and reported";
export const state = c.life;
export const step = (action, _from, to) =>
  action === "DrainPyLandA" && to.statusA === "held" ? "land refuses it" : c.command(action);

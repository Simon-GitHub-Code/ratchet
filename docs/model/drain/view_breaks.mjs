import * as c from "./view_common.mjs";

// Drawn from DrainLanding, where the tool draws only the shortest run to each broken rule.
export const title = "Where the landing run breaks a rule";
export const state = (v) => {
  if (v.half !== "none")
    return v.holdA ? "Cut off half-way, held on the text it left" : "A fast-forward cut off half-way";
  if (v.holdA && v.statusA !== "held" && !v.editedA)
    return "Judged on the tip, its hold released with no edit";
  if (v.mergedA) return "Merged";
  return c.life(v);
};
export const step = c.named;

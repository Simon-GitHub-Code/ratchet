const KEYS = { floor: "gate.floor", ceiling: "ceilings.gate.seconds" };

export const state = (key, word) => (v) => {
  const now = v[key];
  if (now === "absent") return `No ${word}`;
  if (now === "true") return `The ${word} is a flag`;
  return `The ${word} is ${now}`;
};

const loosens = (key, old, now) => {
  if (now === "absent" || (old === "true") !== (now === "true")) return true;
  if (old === "true") return false;
  return key === "ceiling" ? Number(now) > Number(old) : Number(now) < Number(old);
};

const brought = (key, from, to) =>
  to.relaxes.some((line) => {
    const named = line.split(", copy ")[0].match(/^(\S+) from (\S+) to (\S+)$/);
    return (
      !from.relaxes.includes(line) &&
      named[1] === KEYS[key] &&
      named[3] === to[key] &&
      loosens(key, named[2], named[3])
    );
  });

const verb = (old, now) => {
  if (now === "absent") return "removes it";
  if (old === "absent") return "puts it back";
  if (now === "true") return "makes it a flag";
  if (old === "true") return "makes it a number";
  return Number(now) > Number(old) ? "raises it" : "lowers it";
};

export const step = (key) => (_action, from, to) =>
  from[key] === to[key]
    ? "a branch lands"
    : `a branch ${verb(from[key], to[key])}` + (brought(key, from, to) ? ", with its record" : "");

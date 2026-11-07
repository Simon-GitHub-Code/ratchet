/**
 * Architectural fitness tests: the decisions as executable checks (typescript-pnpm scaffold).
 *
 * Adopt copies this file to `tests/fitness/`. Each policy constant cites the ADR that holds it,
 * and one test fails when that ADR is gone. `just limits` runs `-t limits`; `just bite` runs
 * `-t bites`, which feeds each check a synthetic violation and expects it to fail. Every check is
 * a plain function over its inputs, so the bite test can call it without touching the repository.
 */
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..", "..");
const SRC = join(ROOT, "src");
const ADRS = join(ROOT, "docs", "adr");
const MAX_FILE_LINES = 400; // ADR 0001: the binding's Limits, eslint max-lines
const MAX_COMPLEXITY = 10; // ADR 0001: the binding's Limits, eslint complexity
const AVOID = /^_Avoid_:\s*(.+)$/gm;

function sourceFiles(root: string = SRC): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(root)) {
    const path = join(root, entry);
    if (statSync(path).isDirectory()) found.push(...sourceFiles(path));
    else if (/\.(ts|tsx)$/.test(entry) && !entry.endsWith(".d.ts")) found.push(path);
  }
  return found.sort();
}

export function overTheLineLimit(
  files: readonly string[],
  limit: number = MAX_FILE_LINES,
): string[] {
  return files.filter((f) => readFileSync(f, "utf8").split("\n").length > limit);
}

export function bannedWords(glossary: string): Set<string> {
  const words = new Set<string>();
  for (const match of glossary.matchAll(AVOID)) {
    for (const word of (match[1] ?? "").split(",")) {
      const cleaned = word.trim().replace(/^`|`$/g, "").toLowerCase();
      if (cleaned && !cleaned.startsWith("<")) words.add(cleaned);
    }
  }
  return words;
}

export function bannedIdentifiers(source: string, banned: ReadonlySet<string>): string[] {
  const identifiers = source.match(/\b[A-Za-z_$][A-Za-z0-9_$]*\b/g) ?? [];
  const found = new Set<string>();
  for (const name of identifiers) {
    const words = name.split(/_|(?<=[a-z])(?=[A-Z])/);
    if (words.some((w) => banned.has(w.toLowerCase()))) found.add(name);
  }
  return [...found].sort();
}

describe("limits", () => {
  it("files stay within the line limit", () => {
    const over = overTheLineLimit(sourceFiles()).map((f) => relative(ROOT, f));
    expect(over, `split these files (limit ${String(MAX_FILE_LINES)} lines)`).toEqual([]);
  });

  it("eslint holds the same complexity limit as the binding", () => {
    const justfile = readFileSync(join(ROOT, "justfile"), "utf8");
    expect(justfile).toContain(`complexity: [error, ${String(MAX_COMPLEXITY)}]`);
    expect(justfile).toContain(`max-lines: [error, ${String(MAX_FILE_LINES)}]`);
  });
});

describe("records", () => {
  it("every policy constant cites an existing ADR", () => {
    const cited = new Set([...readFileSync(__filename, "utf8").matchAll(/\/\/ ADR (\d{4})/g)].map((m) => m[1]));
    const existing = new Set(readdirSync(ADRS).filter((f) => /^\d{4}-.*\.md$/.test(f)).map((f) => f.slice(0, 4)));
    expect(cited.size).toBeGreaterThan(0);
    expect([...cited].filter((n) => !existing.has(n!))).toEqual([]);
  });

  it("identifiers avoid the glossary's banned words", () => {
    const banned = bannedWords(readFileSync(join(ROOT, "GLOSSARY.md"), "utf8"));
    const offenders = sourceFiles().flatMap((f) =>
      bannedIdentifiers(readFileSync(f, "utf8"), banned).map((n) => `${relative(ROOT, f)}: ${n}`),
    );
    expect(offenders).toEqual([]);
  });
});

describe("bites", () => {
  it("each check fails on its synthetic violation", () => {
    // Why: a check nobody has seen fail may never fire. Extend this with every new check.
    const long = join(ROOT, "node_modules", ".cache", "fitness-long.ts");
    mkdirSync(join(ROOT, "node_modules", ".cache"), { recursive: true });
    writeFileSync(long, "x;\n".repeat(MAX_FILE_LINES + 1));
    expect(overTheLineLimit([long])).toEqual([long]);
    expect(bannedIdentifiers("const clientId = 1; function buyer() {}", new Set(["client", "buyer"]))).toEqual([
      "buyer",
      "clientId",
    ]);
    expect(bannedWords("**Customer**:\nWho buys.\n_Avoid_: client, `buyer`, <synonym>\n")).toEqual(
      new Set(["client", "buyer"]),
    );
  });
});

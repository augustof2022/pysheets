import { tokenizeFormula } from "./components/FormulaInput";

/** Functions where a range that shifts while filling down is almost always a mistake. */
const LOOKUPS = new Set([
  "VLOOKUP",
  "HLOOKUP",
  "XLOOKUP",
  "LOOKUP",
  "MATCH",
  "INDEX",
  "FILTER",
  "COUNTIF",
  "COUNTIFS",
  "SUMIF",
  "SUMIFS",
  "AVERAGEIF",
  "AVERAGEIFS",
  "MAXIFS",
  "MINIFS",
  "COUNTUNIQUE",
  "SUMPRODUCT",
  "QUERY",
  "UNIQUE",
]);

const RANGE = /^((?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?(\$?[A-Za-z]{1,3})(\$?)(\d+):(\$?[A-Za-z]{1,3})(\$?)(\d+)$/;

/**
 * Ranges in a lookup formula that would move down one row per row when the formula is filled
 * down a column (A2:A2001 -> A3:A2002 …), plus the formula with those rows fixed (A$2:A$2001).
 * Null when there is nothing to warn about.
 */
export function shiftingRanges(formula: string): { ranges: string[]; fixed: string } | null {
  if (!formula.startsWith("=")) return null;
  const toks = tokenizeFormula(formula);
  if (!toks.some((t) => t.kind === "func" && LOOKUPS.has(t.text.toUpperCase()))) return null;
  const ranges: string[] = [];
  const out = toks.map((t) => {
    if (t.kind !== "ref") return t.text;
    const m = t.text.match(RANGE);
    if (!m) return t.text;
    const [, sheet = "", c1, d1, r1, c2, d2, r2] = m;
    if (d1 || d2 || Number(r2) <= Number(r1)) return t.text; // a fixed end (running totals) or one row
    ranges.push(t.text);
    return `${sheet}${c1}$${r1}:${c2}$${r2}`;
  });
  return ranges.length ? { ranges, fixed: out.join("") } : null;
}

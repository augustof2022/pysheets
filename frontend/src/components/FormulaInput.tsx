import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store";

export const REF_COLORS = ["#4f46e5", "#e11d48", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

export const FUNC_DOCS: Record<string, [string, string]> = {
  SUM: ["SUM(value1, [value2, ...])", "Adds numbers or ranges"],
  AVERAGE: ["AVERAGE(value1, [value2, ...])", "Arithmetic mean, ignoring text"],
  COUNT: ["COUNT(value1, [value2, ...])", "Counts numeric values"],
  COUNTA: ["COUNTA(value1, [value2, ...])", "Counts non-empty values"],
  COUNTBLANK: ["COUNTBLANK(range)", "Counts empty cells"],
  COUNTIF: ["COUNTIF(range, criterion)", "Counts cells matching a criterion, e.g. \">10\" or \"a*\""],
  COUNTIFS: ["COUNTIFS(range1, criterion1, [range2, criterion2, ...])", "Counts rows matching all criteria"],
  SUMIF: ["SUMIF(range, criterion, [sum_range])", "Sums cells matching a criterion"],
  SUMIFS: ["SUMIFS(sum_range, range1, criterion1, ...)", "Sums rows matching all criteria"],
  AVERAGEIF: ["AVERAGEIF(range, criterion, [average_range])", "Average of matching cells"],
  AVERAGEIFS: ["AVERAGEIFS(average_range, range1, criterion1, ...)", "Average of rows matching all criteria"],
  MAXIFS: ["MAXIFS(max_range, range1, criterion1, ...)", "Maximum among matching rows"],
  MINIFS: ["MINIFS(min_range, range1, criterion1, ...)", "Minimum among matching rows"],
  MIN: ["MIN(value1, [value2, ...])", "Smallest number"],
  MAX: ["MAX(value1, [value2, ...])", "Largest number"],
  MEDIAN: ["MEDIAN(value1, [value2, ...])", "Median value"],
  IF: ["IF(condition, value_if_true, [value_if_false])", "Chooses a value based on a condition"],
  IFS: ["IFS(condition1, value1, [condition2, value2, ...])", "First value whose condition is true"],
  IFERROR: ["IFERROR(value, [value_if_error])", "Fallback when a value is an error"],
  IFNA: ["IFNA(value, value_if_na)", "Fallback when a lookup finds nothing"],
  AND: ["AND(logical1, [logical2, ...])", "TRUE if all are true"],
  OR: ["OR(logical1, [logical2, ...])", "TRUE if any is true"],
  NOT: ["NOT(logical)", "Logical negation"],
  SWITCH: ["SWITCH(expression, case1, value1, [default])", "Matches an expression against cases"],
  VLOOKUP: ["VLOOKUP(search_key, range, index, [is_sorted])", "Vertical lookup — use FALSE for exact match"],
  HLOOKUP: ["HLOOKUP(search_key, range, index, [is_sorted])", "Horizontal lookup"],
  XLOOKUP: ["XLOOKUP(search_key, lookup_range, result_range, [missing_value], [match_mode], [search_mode])", "Modern lookup in any direction"],
  INDEX: ["INDEX(range, [row], [column])", "Value at a position in a range"],
  MATCH: ["MATCH(search_key, range, [search_type])", "Position of a value — use 0 for exact match"],
  CHOOSE: ["CHOOSE(index, choice1, [choice2, ...])", "Pick a value by position"],
  FILTER: ["FILTER(range, condition1, [condition2, ...])", "Rows of a range meeting conditions (spills)"],
  SORT: ["SORT(range, [sort_column], [is_ascending], ...)", "Sorted copy of a range (spills)"],
  SORTN: ["SORTN(range, [n], [display_ties_mode], [sort_column], [is_ascending])", "Top n rows after sorting"],
  UNIQUE: ["UNIQUE(range)", "Distinct rows of a range (spills)"],
  QUERY: ["QUERY(data, query, [headers])", "Google Visualization query, e.g. \"select A, sum(C) group by A\""],
  ARRAYFORMULA: ["ARRAYFORMULA(array_formula)", "Evaluates an expression over whole ranges"],
  SEQUENCE: ["SEQUENCE(rows, [columns], [start], [step])", "Array of sequential numbers"],
  TRANSPOSE: ["TRANSPOSE(range)", "Swaps rows and columns"],
  SPLIT: ["SPLIT(text, delimiter, [split_by_each], [remove_empty_text])", "Splits text into columns"],
  FLATTEN: ["FLATTEN(range1, [range2, ...])", "Flattens ranges into one column"],
  TRIM: ["TRIM(text)", "Removes leading, trailing and repeated spaces"],
  CLEAN: ["CLEAN(text)", "Removes non-printable characters"],
  UPPER: ["UPPER(text)", "Converts to upper case"],
  LOWER: ["LOWER(text)", "Converts to lower case"],
  PROPER: ["PROPER(text)", "Capitalises Each Word"],
  LEN: ["LEN(text)", "Length of text"],
  LEFT: ["LEFT(text, [number_of_characters])", "Leftmost characters"],
  RIGHT: ["RIGHT(text, [number_of_characters])", "Rightmost characters"],
  MID: ["MID(text, start, length)", "Substring"],
  SUBSTITUTE: ["SUBSTITUTE(text, search_for, replace_with, [occurrence])", "Replaces text"],
  REPLACE: ["REPLACE(text, position, length, new_text)", "Replaces part of a string"],
  CONCATENATE: ["CONCATENATE(string1, [string2, ...])", "Joins strings (or whole ranges)"],
  CONCAT: ["CONCAT(value1, value2)", "Joins two values"],
  TEXTJOIN: ["TEXTJOIN(delimiter, ignore_empty, text1, [text2, ...])", "Joins values with a delimiter"],
  JOIN: ["JOIN(delimiter, value_or_array1, ...)", "Joins values with a delimiter"],
  TEXT: ["TEXT(number, format)", "Formats a number or date as text, e.g. \"#,##0.00\""],
  VALUE: ["VALUE(text)", "Converts text to a number"],
  FIND: ["FIND(search_for, text, [starting_at])", "Case-sensitive position of text"],
  SEARCH: ["SEARCH(search_for, text, [starting_at])", "Case-insensitive position of text"],
  REGEXMATCH: ["REGEXMATCH(text, regular_expression)", "TRUE if text matches"],
  REGEXEXTRACT: ["REGEXEXTRACT(text, regular_expression)", "Extracts the first match"],
  REGEXREPLACE: ["REGEXREPLACE(text, regular_expression, replacement)", "Replaces matches"],
  ROUND: ["ROUND(value, [places])", "Rounds half away from zero"],
  ROUNDUP: ["ROUNDUP(value, [places])", "Rounds away from zero"],
  ROUNDDOWN: ["ROUNDDOWN(value, [places])", "Rounds towards zero"],
  ABS: ["ABS(value)", "Absolute value"],
  MOD: ["MOD(dividend, divisor)", "Remainder"],
  POWER: ["POWER(base, exponent)", "Exponentiation"],
  SQRT: ["SQRT(value)", "Square root"],
  SUMPRODUCT: ["SUMPRODUCT(array1, [array2, ...])", "Sum of element-wise products"],
  DATE: ["DATE(year, month, day)", "Builds a date"],
  TODAY: ["TODAY()", "Current date"],
  NOW: ["NOW()", "Current date and time"],
  YEAR: ["YEAR(date)", "Year of a date"],
  MONTH: ["MONTH(date)", "Month of a date"],
  DAY: ["DAY(date)", "Day of month"],
  WEEKDAY: ["WEEKDAY(date, [type])", "Day of week"],
  EOMONTH: ["EOMONTH(start_date, months)", "Last day of a month"],
  EDATE: ["EDATE(start_date, months)", "Date shifted by months"],
  DATEDIF: ["DATEDIF(start_date, end_date, unit)", "Difference in D, M or Y"],
  NETWORKDAYS: ["NETWORKDAYS(start_date, end_date)", "Working days between dates"],
  ISBLANK: ["ISBLANK(value)", "TRUE if empty"],
  ISNUMBER: ["ISNUMBER(value)", "TRUE if numeric"],
  ISTEXT: ["ISTEXT(value)", "TRUE if text"],
  ISERROR: ["ISERROR(value)", "TRUE if an error"],
  ISEMAIL: ["ISEMAIL(value)", "TRUE if a valid e-mail address"],
  ROW: ["ROW([cell_reference])", "Row number"],
  COLUMN: ["COLUMN([cell_reference])", "Column number"],
  ROWS: ["ROWS(range)", "Number of rows"],
  COLUMNS: ["COLUMNS(range)", "Number of columns"],
  COUNTUNIQUE: ["COUNTUNIQUE(value1, [value2, ...])", "Counts distinct values"],
  LARGE: ["LARGE(data, n)", "n-th largest value"],
  SMALL: ["SMALL(data, n)", "n-th smallest value"],
  STDEV: ["STDEV(value1, [value2, ...])", "Sample standard deviation"],
  PERCENTILE: ["PERCENTILE(data, percentile)", "Value at a percentile"],
  HYPERLINK: ["HYPERLINK(url, [link_label])", "Displays a link label"],
  RAND: ["RAND()", "Random number between 0 and 1"],
  RANDBETWEEN: ["RANDBETWEEN(low, high)", "Random integer"],
};

export type Tok = { kind: "ref" | "func" | "str" | "num" | "op" | "text" | "err" | "bool"; text: string; color?: string };

const TOKEN_RE =
  /("(?:[^"]|"")*"?)|((?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?(\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+|:\$?[A-Za-z]{1,3})?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|\$?\d+:\$?\d+)(?![\w(])|([A-Za-z_][\w.]*)(?=\s*\()|(\d+\.?\d*(?:[eE][-+]?\d+)?)|(TRUE|FALSE)\b|(#[A-Z/0!?]+)|([-+*/^&=<>(),;:{}%]+)|(\s+|.)/gi;

export function tokenizeFormula(text: string): Tok[] {
  if (!text.startsWith("=")) return [{ kind: "text", text }];
  const out: Tok[] = [{ kind: "op", text: "=" }];
  const colorOf = new Map<string, string>();
  let m: RegExpExecArray | null;
  const body = text.slice(1);
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(body))) {
    if (m[1] !== undefined) out.push({ kind: "str", text: m[1] });
    else if (m[3] !== undefined) {
      const full = (m[2] || "") + m[3];
      const key = full.replace(/\$/g, "").toUpperCase();
      if (!colorOf.has(key)) colorOf.set(key, REF_COLORS[colorOf.size % REF_COLORS.length]);
      out.push({ kind: "ref", text: full, color: colorOf.get(key) });
    } else if (m[4] !== undefined) out.push({ kind: "func", text: m[4] });
    else if (m[5] !== undefined) out.push({ kind: "num", text: m[5] });
    else if (m[6] !== undefined) out.push({ kind: "bool", text: m[6] });
    else if (m[7] !== undefined) out.push({ kind: "err", text: m[7] });
    else if (m[8] !== undefined) out.push({ kind: "op", text: m[8] });
    else out.push({ kind: "text", text: m[0] });
    if (m[0].length === 0) TOKEN_RE.lastIndex++;
  }
  return out;
}

/** References in a formula with their highlight colours (for the grid overlay). */
export function formulaRefs(text: string): { ref: string; color: string }[] {
  return tokenizeFormula(text)
    .filter((t) => t.kind === "ref")
    .map((t) => ({ ref: t.text, color: t.color! }));
}

/** Can a reference be inserted at the caret (formula mode, after an operator)? */
export function canInsertRef(text: string, caret: number): boolean {
  if (!text.startsWith("=")) return false;
  const before = text.slice(0, caret).replace(/\s+$/, "");
  if (before === "=") return true;
  return /[-+*/^&=<>(,;:{%]$/.test(before);
}

function acContext(text: string, caret: number, functions: string[]) {
  if (!text.startsWith("=")) return null;
  const before = text.slice(0, caret);
  // inside a string literal?
  if ((before.match(/"/g) || []).length % 2 === 1) return null;
  const m = before.match(/(^|[-+*/^&=<>(,;:{\s])([A-Za-z][A-Za-z0-9_.]*)$/);
  let items: string[] = [];
  let prefix = "";
  if (m) {
    prefix = m[2];
    const up = prefix.toUpperCase();
    items = functions.filter((f) => f.startsWith(up)).slice(0, 8);
    if (items.length === 1 && items[0] === up) items = [];
  }
  // signature help: innermost unclosed function
  let depth = 0;
  let argIdx = 0;
  let fn: string | null = null;
  let inStr = false;
  for (let i = before.length - 1; i >= 0; i--) {
    const ch = before[i];
    if (ch === '"') inStr = !inStr;
    if (inStr) continue;
    if (ch === ")") depth++;
    else if (ch === "(") {
      if (depth === 0) {
        const nm = before.slice(0, i).match(/([A-Za-z][A-Za-z0-9_.]*)\s*$/);
        fn = nm ? nm[1].toUpperCase() : null;
        break;
      }
      depth--;
    } else if ((ch === "," || ch === ";") && depth === 0) argIdx++;
  }
  return { prefix, items, fn, argIdx };
}

interface Props {
  value: string;
  caret?: number;
  onChange: (text: string, caret: number) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>, acOpen: boolean) => void;
  onFocus?: () => void;
  onBlur?: () => void;
  className?: string;
  autoFocus?: boolean;
  placeholder?: string;
  minWidth?: number;
  inputRef?: React.MutableRefObject<HTMLTextAreaElement | null>;
  readOnly?: boolean;
}

export default function FormulaInput(p: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const functions = useApp((s) => s.functions);
  const [caret, setCaret] = useState(p.caret ?? p.value.length);
  const [acIndex, setAcIndex] = useState(0);
  const [focused, setFocused] = useState(false);
  const toks = useMemo(() => tokenizeFormula(p.value), [p.value]);
  const ac = useMemo(() => (focused ? acContext(p.value, caret, functions) : null), [p.value, caret, functions, focused]);
  const acOpen = !!ac && ac.items.length > 0;

  useEffect(() => {
    if (p.inputRef) p.inputRef.current = ref.current;
  });

  useLayoutEffect(() => {
    if (p.autoFocus && ref.current) {
      ref.current.focus();
      const pos = p.caret ?? p.value.length;
      ref.current.setSelectionRange(pos, pos);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.autoFocus]);

  useLayoutEffect(() => {
    if (p.caret !== undefined && ref.current && document.activeElement === ref.current) {
      if (ref.current.selectionStart !== p.caret) ref.current.setSelectionRange(p.caret, p.caret);
    }
  }, [p.caret, p.value]);

  useEffect(() => setAcIndex(0), [ac?.prefix]);

  const accept = (name: string) => {
    if (!ac) return;
    const start = caret - ac.prefix.length;
    const next = p.value.slice(0, start) + name + "(" + p.value.slice(caret);
    const pos = start + name.length + 1;
    p.onChange(next, pos);
    setCaret(pos);
  };

  const rect = ref.current?.getBoundingClientRect();
  const sig = ac?.fn ? FUNC_DOCS[ac.fn] : null;

  return (
    <div className={`fi ${p.className || ""}`} style={{ minWidth: p.minWidth }}>
      <div className="fi-mirror" aria-hidden>
        {toks.map((t, i) => (
          <span key={i} className={`tk-${t.kind}`} style={t.color ? { color: t.color } : undefined}>
            {t.text}
          </span>
        ))}
        {p.value.length === 0 && p.placeholder ? <span className="fi-ph">{p.placeholder}</span> : null}
        <span>&#8203;</span>
      </div>
      <textarea
        ref={ref}
        className="fi-input"
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        readOnly={p.readOnly}
        value={p.value}
        rows={1}
        onChange={(e) => {
          const pos = e.target.selectionStart ?? e.target.value.length;
          setCaret(pos);
          p.onChange(e.target.value, pos);
        }}
        onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
        onFocus={() => {
          setFocused(true);
          p.onFocus?.();
        }}
        onBlur={() => {
          setFocused(false);
          p.onBlur?.();
        }}
        onKeyDown={(e) => {
          if (acOpen) {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setAcIndex((i) => (i + 1) % ac!.items.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setAcIndex((i) => (i - 1 + ac!.items.length) % ac!.items.length);
              return;
            }
            if (e.key === "Tab" || (e.key === "Enter" && !e.ctrlKey && !e.metaKey)) {
              e.preventDefault();
              accept(ac!.items[acIndex]);
              return;
            }
          }
          p.onKeyDown?.(e, acOpen);
        }}
      />
      {focused && rect && (acOpen || sig) &&
        createPortal(
          <div className="ac-pop" style={{ left: Math.min(rect.left, window.innerWidth - 380), top: rect.bottom + 6 }}>
            {sig && (
              <div className="ac-sig">
                <SigLine sig={sig[0]} arg={ac!.argIdx} />
                <div className="ac-sig-doc">{sig[1]}</div>
              </div>
            )}
            {acOpen &&
              ac!.items.map((f, i) => (
                <div
                  key={f}
                  className={`ac-item ${i === acIndex ? "on" : ""}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    accept(f);
                  }}
                  onMouseEnter={() => setAcIndex(i)}
                >
                  <span className="ac-name">{f}</span>
                  <span className="ac-doc">{FUNC_DOCS[f]?.[1] || ""}</span>
                </div>
              ))}
          </div>,
          document.body,
        )}
    </div>
  );
}

function SigLine({ sig, arg }: { sig: string; arg: number }) {
  const m = sig.match(/^(\w[\w.]*)\((.*)\)$/);
  if (!m) return <span>{sig}</span>;
  const args = m[2].split(/,\s*/);
  const repeating = args.findIndex((a) => a.includes("..."));
  const active = repeating >= 0 && arg >= repeating ? repeating - 1 + ((arg - repeating + 1) % 2 === 0 ? 1 : 0) : arg;
  return (
    <span className="ac-sigline">
      <b>{m[1]}</b>(
      {args.map((a, i) => (
        <span key={i}>
          {i > 0 && ", "}
          <span className={i === active ? "ac-arg-on" : ""}>{a}</span>
        </span>
      ))}
      )
    </span>
  );
}

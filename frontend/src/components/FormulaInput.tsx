import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { activeArg, argName, customDoc, getDoc, syntaxArgs, type FnDoc } from "../formulaDocs";
import { localStorageSet, openDialog, useApp } from "../store";

let helpOpenPref = (() => {
  try {
    return localStorage.getItem("pysheets.fnHelp") !== "closed";
  } catch {
    return true;
  }
})();

export const REF_COLORS = ["#4f46e5", "#e11d48", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

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
  const customDocs = useApp((s) => s.info?.custom_function_docs);
  const [helpOpen, setHelpOpen] = useState(helpOpenPref);
  const toggleHelp = () => {
    helpOpenPref = !helpOpen;
    localStorageSet("pysheets.fnHelp", helpOpenPref ? "open" : "closed");
    setHelpOpen(helpOpenPref);
  };
  const docFor = (name: string): FnDoc | null => {
    const d = getDoc(name);
    if (d) return d;
    const c = customDocs?.[name];
    return c ? customDoc(name, c.params, c.doc) : null;
  };
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
  // the function the caret is inside (help card), or the highlighted autocomplete item
  const helpDoc = ac?.fn ? docFor(ac.fn) : null;
  const listDoc = acOpen ? docFor(ac!.items[acIndex]) : null;
  const below = rect ? rect.bottom + 340 < window.innerHeight || rect.top < 340 : true;
  const popStyle: React.CSSProperties = rect
    ? below
      ? { left: Math.max(8, Math.min(rect.left, window.innerWidth - 420)), top: rect.bottom + 6 }
      : { left: Math.max(8, Math.min(rect.left, window.innerWidth - 420)), bottom: window.innerHeight - rect.top + 6 }
    : {};

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
          if (e.key === "F1" && helpDoc) {
            e.preventDefault();
            toggleHelp();
            return;
          }
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
      {focused && rect && (acOpen || helpDoc) &&
        createPortal(
          <div className="ac-pop" style={popStyle} onMouseDown={(e) => e.preventDefault()}>
            {acOpen ? (
              <>
                {ac!.items.map((f, i) => (
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
                    <span className="ac-doc">{docFor(f)?.summary || ""}</span>
                  </div>
                ))}
                {listDoc && (
                  <div className="ac-preview">
                    <code>{listDoc.syntax}</code>
                    <span>Tab or Enter to insert</span>
                  </div>
                )}
              </>
            ) : (
              helpDoc && <HelpCard doc={helpDoc} argIdx={ac!.argIdx} open={helpOpen} onToggle={toggleHelp} />
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}

/** Google Sheets-style formula help: syntax with the current argument, example, summary, arguments. */
function HelpCard({ doc, argIdx, open, onToggle }: { doc: FnDoc; argIdx: number; open: boolean; onToggle: () => void }) {
  const tokens = syntaxArgs(doc.syntax);
  const active = activeArg(tokens, argIdx);
  const name = doc.syntax.slice(0, doc.syntax.indexOf("("));
  const body = useRef<HTMLDivElement>(null);
  // keep the explanation of the argument being typed in view
  useEffect(() => {
    const b = body.current;
    const on = b?.querySelector<HTMLElement>(".fh-arg.on");
    if (!b || !on) return;
    const top = on.offsetTop; // .fh-body is position: relative
    if (top < b.scrollTop || top + on.offsetHeight > b.scrollTop + b.clientHeight) {
      b.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    }
  }, [active, open]);
  let inBrackets = false; // "[criteria_range2, criterion2, ...]": every token in the group is optional
  const argRows = tokens
    .map((t, i) => {
      if (t.startsWith("[")) inBrackets = true;
      const row = { i, name: argName(t), optional: inBrackets, desc: doc.args[i] };
      if (t.endsWith("]")) inBrackets = false;
      return row;
    })
    .filter((a) => a.desc && a.name !== "…");
  return (
    <div className="fh">
      <button className="fh-head" onClick={onToggle} data-tip={open ? "Hide formula help (F1)" : "Show formula help (F1)"}>
        <span className="ac-sigline">
          <b>{name}</b>(
          {tokens.map((t, i) => (
            <span key={i}>
              {i > 0 && ", "}
              <span className={i === active ? "ac-arg-on" : ""}>{t}</span>
            </span>
          ))}
          )
        </span>
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && (
        <div className="fh-body" ref={body}>
          <div className="fh-sec">
            <div className="fh-label">Example</div>
            <code className="fh-example">={doc.example}</code>
          </div>
          <div className="fh-sec">
            <div className="fh-label">Summary</div>
            <div>{doc.summary}</div>
          </div>
          {argRows.map((a) => (
            <div key={a.i} className={`fh-arg ${a.i === active ? "on" : ""}`}>
              <div className="fh-argname">
                {a.name}
                {a.optional && <span className="fh-opt">optional</span>}
              </div>
              <div className="fh-argdesc">{a.desc}</div>
            </div>
          ))}
          <div className="fh-foot">
            <span>{doc.category}</span>
            <button className="link" onClick={() => openDialog("docs", { fn: name })}>
              Learn more
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

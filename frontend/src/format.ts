import type { ColType } from "./types";

export type Align = "left" | "center" | "right";

export interface Formatted {
  text: string;
  align: Align;
  negative?: boolean;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

const NUMERIC_RE = /^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$/;

export function autoNumber(n: number): string {
  if (!isFinite(n)) return "#NUM!";
  if (Number.isInteger(n) && Math.abs(n) < 1e15) return String(n);
  const abs = Math.abs(n);
  if (abs !== 0 && (abs >= 1e15 || abs < 1e-9)) return n.toExponential(4).replace(/\.?0+e/, "e");
  // trim floating noise (4.199999999999999 -> 4.2, 40000.00000003 -> 40000)
  const s = parseFloat(n.toPrecision(10)).toString();
  return s;
}

/** Shorten an automatic-format number until `fits` accepts it (Sheets-style): fewer decimals, then scientific. */
export function fitNumber(n: number, fits: (s: string) => boolean): string | null {
  if (!isFinite(n)) return null;
  const abs = Math.abs(n);
  const intDigits = abs >= 1 ? Math.floor(Math.log10(abs)) + 1 : 1;
  for (let dec = Math.max(0, 10 - intDigits); dec >= 0; dec--) {
    const s = parseFloat(n.toFixed(dec)).toString();
    if (s.includes("e")) break;
    if (fits(s)) return s;
  }
  for (let p = 6; p >= 1; p--) {
    const s = n.toExponential(p - 1).replace(/\.?0+e/, "e");
    if (fits(s)) return s;
  }
  return null;
}

export function isNumericText(s: string): boolean {
  return NUMERIC_RE.test(s);
}

interface NumFmt {
  prefix: string;
  suffix: string;
  minDec: number;
  maxDec: number;
  thousands: boolean;
  percent: boolean;
  sci: boolean;
  negParens: boolean;
}

const fmtCache = new Map<string, NumFmt | null>();

function parseNumFmt(fmt: string): NumFmt | null {
  if (fmtCache.has(fmt)) return fmtCache.get(fmt)!;
  let f = fmt.split(";")[0];
  const negParens = fmt.includes(";(") || fmt.includes(";[Red](");
  const m = f.match(/^([^0#.,]*)([#0,]*)(?:\.([0#]+))?(E\+0+)?(%?)(.*)$/);
  let out: NumFmt | null = null;
  if (m && (m[2] || m[3])) {
    const dec = m[3] || "";
    out = {
      prefix: m[1].replace(/"/g, "").replace(/\\/g, ""),
      suffix: (m[6] || "").replace(/"/g, "").replace(/\\/g, ""),
      minDec: (dec.match(/0/g) || []).length,
      maxDec: dec.length,
      thousands: m[2].includes(","),
      percent: m[5] === "%",
      sci: !!m[4],
      negParens,
    };
  }
  f = "";
  fmtCache.set(fmt, out);
  return out;
}

export function formatNumber(n: number, fmt: string | null | undefined): string {
  if (!fmt || fmt === "General" || fmt === "auto") return autoNumber(n);
  if (fmt === "@") return autoNumber(n);
  if (/[ymdhs]/i.test(fmt) && !/[#0]/.test(fmt)) {
    // number formatted as date (serial)
    const d = new Date(Date.UTC(1899, 11, 30) + n * 86400000);
    return formatDate(d, fmt);
  }
  const p = parseNumFmt(fmt);
  if (!p) return autoNumber(n);
  let v = p.percent ? n * 100 : n;
  const neg = v < 0;
  v = Math.abs(v);
  let body: string;
  if (p.sci) {
    body = v.toExponential(p.maxDec).toUpperCase().replace(/E([+-])(\d)$/, "E$10$2");
  } else {
    body = v.toFixed(p.maxDec);
    if (p.maxDec > p.minDec) {
      // trim optional (#) decimals
      let [i, d] = body.split(".");
      d = (d || "").replace(/0+$/, "");
      while (d.length < p.minDec) d += "0";
      body = d ? `${i}.${d}` : i;
    }
    if (p.thousands) {
      const [i, d] = body.split(".");
      body = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (d !== undefined ? "." + d : "");
    }
  }
  const s = `${p.prefix}${body}${p.percent ? "%" : ""}${p.suffix}`;
  if (neg) return p.negParens ? `(${s})` : `-${s}`;
  return s;
}

function pad(n: number, w = 2) {
  return String(n).padStart(w, "0");
}

export function formatDate(d: Date, fmt: string): string {
  const y = d.getUTCFullYear();
  const mo = d.getUTCMonth();
  const day = d.getUTCDate();
  const h = d.getUTCHours();
  const mi = d.getUTCMinutes();
  const s = d.getUTCSeconds();
  const wd = d.getUTCDay();
  const ampm = /am\/pm/i.test(fmt);
  let out = "";
  let i = 0;
  const lower = fmt.toLowerCase();
  let lastWasHour = false;
  while (i < fmt.length) {
    const rest = lower.slice(i);
    const take = (tok: string, val: string) => {
      out += val;
      i += tok.length;
    };
    if (rest.startsWith("yyyy")) take("yyyy", String(y));
    else if (rest.startsWith("yy")) take("yy", pad(y % 100));
    else if (rest.startsWith("mmmm")) take("mmmm", MONTHS_LONG[mo]);
    else if (rest.startsWith("mmm")) take("mmm", MONTHS[mo]);
    else if (rest.startsWith("mm") || rest.startsWith("m")) {
      const tok = rest.startsWith("mm") ? "mm" : "m";
      const isMinute = lastWasHour || /^m+:?s/.test(rest) || (out.endsWith(":") && lastWasHour);
      if (isMinute) take(tok, tok === "mm" ? pad(mi) : String(mi));
      else take(tok, tok === "mm" ? pad(mo + 1) : String(mo + 1));
      lastWasHour = false;
      continue;
    } else if (rest.startsWith("dddd")) take("dddd", DAYS_LONG[wd]);
    else if (rest.startsWith("ddd")) take("ddd", DAYS[wd]);
    else if (rest.startsWith("dd")) take("dd", pad(day));
    else if (rest.startsWith("d")) take("d", String(day));
    else if (rest.startsWith("hh") || rest.startsWith("h")) {
      const tok = rest.startsWith("hh") ? "hh" : "h";
      const hv = ampm ? ((h + 11) % 12) + 1 : h;
      take(tok, tok === "hh" ? pad(hv) : String(hv));
      lastWasHour = true;
      if (fmt[i] === ":") {
        out += ":";
        i++;
      }
      continue;
    } else if (rest.startsWith("ss")) take("ss", pad(s));
    else if (rest.startsWith("am/pm")) take("am/pm", h < 12 ? "AM" : "PM");
    else {
      out += fmt[i];
      i++;
    }
    lastWasHour = false;
  }
  return out;
}

function parseIsoDate(s: string): Date | null {
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)));
}

export function formatCell(v: unknown, type: ColType | undefined, fmt?: string | null): Formatted {
  if (v === null || v === undefined || v === "") return { text: "", align: "left" };
  if (typeof v === "boolean") return { text: v ? "TRUE" : "FALSE", align: "center" };
  if (typeof v === "number") {
    if (fmt === "@") return { text: autoNumber(v), align: "left" };
    return { text: formatNumber(v, fmt), align: "right", negative: v < 0 };
  }
  const s = String(v);
  if (type === "date" || type === "ts") {
    if (fmt && fmt !== "auto") {
      const d = parseIsoDate(s);
      if (d) return { text: formatDate(d, fmt), align: "right" };
    }
    return { text: type === "ts" ? s.replace(/\.\d+$/, "") : s, align: "right" };
  }
  if (s.startsWith("#") && /^#(N\/A|REF!|VALUE!|DIV\/0!|NAME\?|NUM!|ERROR!|NULL!)/.test(s)) {
    return { text: s, align: "center" };
  }
  if (type !== "str" && NUMERIC_RE.test(s)) {
    const n = Number(s);
    return { text: formatNumber(n, fmt), align: "right", negative: n < 0 };
  }
  if (fmt && fmt !== "@" && NUMERIC_RE.test(s)) {
    const n = Number(s);
    return { text: formatNumber(n, fmt), align: "right", negative: n < 0 };
  }
  return { text: s, align: NUMERIC_RE.test(s) ? "right" : "left" };
}

export function colLetters(i: number): string {
  let s = "";
  let n = i + 1;
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

export function cellRef(r: number, c: number): string {
  return `${colLetters(c)}${r + 1}`;
}

export function parseRef(text: string): { r1: number; c1: number; r2: number; c2: number; sheet?: string } | null {
  let t = text.trim();
  let sheet: string | undefined;
  const bang = t.lastIndexOf("!");
  if (bang >= 0) {
    sheet = t.slice(0, bang).replace(/^'(.*)'$/, "$1").replace(/''/g, "'");
    t = t.slice(bang + 1);
  }
  const part = (p: string) => {
    const m = p.replace(/\$/g, "").toUpperCase().match(/^([A-Z]{1,3})?(\d+)?$/);
    if (!m || (!m[1] && !m[2])) return null;
    let c: number | null = null;
    if (m[1]) {
      c = 0;
      for (const ch of m[1]) c = c * 26 + (ch.charCodeAt(0) - 64);
      c -= 1;
    }
    return { r: m[2] ? +m[2] - 1 : null, c };
  };
  const [a, b] = t.split(":");
  const pa = part(a);
  if (!pa) return null;
  const pb = b !== undefined ? part(b) : pa;
  if (!pb) return null;
  const r1 = pa.r ?? 0;
  const r2 = pb.r ?? Number.MAX_SAFE_INTEGER;
  const c1 = pa.c ?? 0;
  const c2 = pb.c ?? Number.MAX_SAFE_INTEGER;
  return { r1: Math.min(r1, r2), r2: Math.max(r1, r2), c1: Math.min(c1, c2), c2: Math.max(c1, c2), sheet };
}

export function rangeLabel(r: { r1: number; c1: number; r2: number; c2: number }): string {
  if (r.r1 === r.r2 && r.c1 === r.c2) return cellRef(r.r1, r.c1);
  return `${cellRef(r.r1, r.c1)}:${cellRef(r.r2, r.c2)}`;
}

export const NUMBER_FORMATS: { label: string; fmt: string | null; sample: string }[] = [
  { label: "Automatic", fmt: null, sample: "1000.12" },
  { label: "Number", fmt: "#,##0.00", sample: "1,000.12" },
  { label: "Integer", fmt: "#,##0", sample: "1,000" },
  { label: "Percent", fmt: "0.00%", sample: "10.12%" },
  { label: "Scientific", fmt: "0.00E+00", sample: "1.01E+03" },
  { label: "Currency", fmt: "$#,##0.00", sample: "$1,000.12" },
  { label: "Euro", fmt: "€#,##0.00", sample: "€1,000.12" },
  { label: "Accounting", fmt: "$#,##0.00;($#,##0.00)", sample: "($1,000.12)" },
  { label: "Date", fmt: "yyyy-mm-dd", sample: "2026-09-26" },
  { label: "Date (US)", fmt: "mm/dd/yyyy", sample: "09/26/2026" },
  { label: "Date (long)", fmt: "mmmm d, yyyy", sample: "September 26, 2026" },
  { label: "Date time", fmt: "yyyy-mm-dd hh:mm:ss", sample: "2026-09-26 15:59:00" },
  { label: "Time", fmt: "h:mm am/pm", sample: "3:59 PM" },
  { label: "Plain text", fmt: "@", sample: "1000.12" },
];

export function bumpDecimals(fmt: string | null | undefined, delta: number, sample?: number): string {
  let f = fmt || "";
  if (!f || f === "auto") {
    const dec = sample !== undefined && !Number.isInteger(sample) ? Math.min(autoNumber(sample).split(".")[1]?.length || 0, 10) : 0;
    f = dec ? "0." + "0".repeat(dec) : "0";
  }
  const m = f.match(/^(.*?)([#0,]*0)(?:\.(0*))?(.*)$/);
  if (!m) return f;
  const dec = Math.max(0, (m[3] || "").length + delta);
  return `${m[1]}${m[2]}${dec ? "." + "0".repeat(dec) : ""}${m[4]}`;
}

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * One app-wide tooltip for every element with `data-tip` (and optional `data-tip-desc`).
 * Rendered in a fixed layer on <body>, so scrolling/clipping containers like the toolbar
 * can't hide it. A tip like "Bold (Ctrl+B)" shows the shortcut as key caps.
 */
interface TipState {
  title: string;
  keys: string[];
  desc: string | null;
  x: number;
  y: number;
  below: boolean;
}

const SHOW_DELAY = 380; // first tooltip
const WARM_MS = 600; // moving to a neighbour within this time shows it at once

function parse(tip: string): { title: string; keys: string[] } {
  const m = tip.match(/^(.*?)\s*\(((?:Ctrl|Cmd|⌘|Alt|Shift|F\d)[^)]*)\)\s*$/);
  if (!m) return { title: tip, keys: [] };
  return { title: m[1], keys: m[2].split("+").map((k) => k.trim()) };
}

export default function Tooltip() {
  const [tip, setTip] = useState<TipState | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [shift, setShift] = useState(0);

  useEffect(() => {
    let timer = 0;
    let current: HTMLElement | null = null;
    let lastHide = 0;

    const show = (el: HTMLElement) => {
      const text = el.dataset.tip;
      if (!text || !el.isConnected) return;
      const r = el.getBoundingClientRect();
      const below = !el.closest(".tabs-bar, .status") && r.bottom + 90 < window.innerHeight;
      setShift(0);
      setTip({ ...parse(text), desc: el.dataset.tipDesc || null, x: r.left + r.width / 2, y: below ? r.bottom + 8 : r.top - 8, below });
    };
    const hide = () => {
      window.clearTimeout(timer);
      if (current) lastHide = performance.now();
      current = null;
      setTip(null);
    };
    const over = (e: MouseEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.<HTMLElement>("[data-tip]") ?? null;
      if (el === current) return;
      window.clearTimeout(timer);
      if (!el) return hide();
      const warm = current !== null || performance.now() - lastHide < WARM_MS;
      current = el;
      setTip(null);
      if (warm) show(el);
      else timer = window.setTimeout(() => current === el && show(el), SHOW_DELAY);
    };
    const leaveWindow = (e: MouseEvent) => {
      if (!e.relatedTarget) hide();
    };
    document.addEventListener("mouseover", over);
    document.addEventListener("mouseout", leaveWindow);
    document.addEventListener("mousedown", hide, true);
    document.addEventListener("keydown", hide, true);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("mouseover", over);
      document.removeEventListener("mouseout", leaveWindow);
      document.removeEventListener("mousedown", hide, true);
      document.removeEventListener("keydown", hide, true);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, []);

  // keep the bubble inside the window
  useEffect(() => {
    if (!tip || !box.current) return;
    const r = box.current.getBoundingClientRect();
    const pad = 8;
    if (r.left < pad) setShift(pad - r.left);
    else if (r.right > window.innerWidth - pad) setShift(window.innerWidth - pad - r.right);
  }, [tip]);

  if (!tip) return null;
  return createPortal(
    <div
      ref={box}
      className={`tip ${tip.below ? "below" : "above"} ${tip.desc ? "rich" : ""}`}
      style={{ left: tip.x + shift, top: tip.y }}
      role="tooltip"
    >
      <div className="tip-head">
        <span className="tip-title">{tip.title}</span>
        {tip.keys.length > 0 && (
          <span className="tip-keys">
            {tip.keys.map((k, i) => (
              <kbd key={i}>{k}</kbd>
            ))}
          </span>
        )}
      </div>
      {tip.desc && <div className="tip-desc">{tip.desc}</div>}
    </div>,
    document.body,
  );
}

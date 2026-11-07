import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MenuItem } from "../store";
import Icon from "./Icon";

/** Keeps an element mounted while its exit animation plays. */
export function usePresence(open: boolean, ms = 140) {
  const [mounted, setMounted] = useState(open);
  const [state, setState] = useState<"open" | "closed">(open ? "open" : "closed");
  useEffect(() => {
    if (open) {
      setMounted(true);
      const id = requestAnimationFrame(() => setState("open"));
      return () => cancelAnimationFrame(id);
    }
    setState("closed");
    const t = window.setTimeout(() => setMounted(false), ms);
    return () => window.clearTimeout(t);
  }, [open, ms]);
  return { mounted, state };
}

interface MenuListProps {
  items: MenuItem[];
  x: number;
  y: number;
  onClose: () => void;
  minWidth?: number;
  sub?: boolean;
  anchorRight?: number; // for submenus: x of the parent's right edge
}

export function MenuList({ items, x, y, onClose, minWidth = 220, sub, anchorRight }: MenuListProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  const [active, setActive] = useState(-1);
  const [subOpen, setSubOpen] = useState<{ i: number; x: number; y: number; right: number } | null>(null);
  const timer = useRef(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = x;
    let ny = y;
    if (nx + r.width > window.innerWidth - 8) {
      // submenus flip to the left of their parent; menus slide back on screen
      nx = sub && anchorRight !== undefined ? anchorRight - r.width - 4 : window.innerWidth - r.width - 8;
    }
    if (ny + r.height > window.innerHeight - 8) ny = Math.max(8, window.innerHeight - r.height - 8);
    setPos({ x: Math.max(8, nx), y: ny });
  }, [x, y, sub, anchorRight]);

  const run = (it: MenuItem) => {
    if (it.disabled || it.items) return;
    onClose();
    it.run?.();
  };

  useEffect(() => {
    if (sub) return;
    const onKey = (e: KeyboardEvent) => {
      const enabled = items.map((it, i) => (!it.divider && !it.disabled ? i : -1)).filter((i) => i >= 0);
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const cur = enabled.indexOf(active);
        const next = e.key === "ArrowDown" ? enabled[(cur + 1) % enabled.length] : enabled[(cur - 1 + enabled.length) % enabled.length];
        setActive(next);
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault();
        run(items[active]);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  return (
    <div ref={ref} className="menu pop-in" style={{ left: pos.x, top: pos.y, minWidth }} onContextMenu={(e) => e.preventDefault()}>
      {items.map((it, i) =>
        it.divider ? (
          <div key={i} className="menu-div" />
        ) : (
          <div
            key={i}
            className={`menu-item ${it.disabled ? "disabled" : ""} ${it.danger ? "danger" : ""} ${active === i ? "active" : ""}`}
            onMouseEnter={(e) => {
              setActive(i);
              window.clearTimeout(timer.current);
              const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
              if (it.items) timer.current = window.setTimeout(() => setSubOpen({ i, x: r.right + 2, y: r.top - 5, right: r.left }), 80);
              else timer.current = window.setTimeout(() => setSubOpen(null), 120);
            }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => {
              if (it.items) {
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setSubOpen({ i, x: r.right + 2, y: r.top - 5, right: r.left });
                return;
              }
              run(it);
            }}
          >
            <span className="menu-icon">{it.checked ? <Icon name="check" size={15} /> : <Icon name={it.icon} size={15} />}</span>
            <span className="menu-label">{it.label}</span>
            {it.shortcut && <span className="menu-kbd">{it.shortcut}</span>}
            {it.items && <Icon name="chevron-right" size={14} className="menu-chev" />}
          </div>
        ),
      )}
      {subOpen && items[subOpen.i]?.items && (
        <MenuList items={items[subOpen.i].items!} x={subOpen.x} y={subOpen.y} onClose={onClose} sub anchorRight={subOpen.right} minWidth={180} />
      )}
    </div>
  );
}

export function FloatingMenu({ items, x, y, onClose }: { items: MenuItem[]; x: number; y: number; onClose: () => void }) {
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!t.closest(".menu")) onClose();
    };
    const onBlur = () => onClose();
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("blur", onBlur);
    window.addEventListener("resize", onBlur);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("resize", onBlur);
    };
  }, [onClose]);
  return createPortal(<MenuList items={items} x={x} y={y} onClose={onClose} />, document.body);
}

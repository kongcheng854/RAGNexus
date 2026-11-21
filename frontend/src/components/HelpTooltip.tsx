import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CircleHelp } from "lucide-react";

export function HelpTooltip({ label, children }: { label: string; children: string }) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number; above: boolean } | null>(null);

  function show() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(260, window.innerWidth - 24);
    setPosition({
      left: Math.max(12, Math.min(rect.left - 20, window.innerWidth - width - 12)),
      top: window.innerHeight - rect.bottom < 140 ? rect.top - 8 : rect.bottom + 8,
      above: window.innerHeight - rect.bottom < 140,
    });
  }

  useEffect(() => {
    if (!position) return;
    const close = () => setPosition(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [position]);

  return (
    <span className="help-tooltip" onMouseEnter={show} onMouseLeave={() => setPosition(null)}>
      <button
        ref={trigger}
        type="button"
        aria-label={`关于 ${label}`}
        aria-describedby={position ? id : undefined}
        className="help-trigger"
        onFocus={show}
        onBlur={() => setPosition(null)}
        onKeyDown={(event) => { if (event.key === "Escape") setPosition(null); }}
        onClick={show}
      ><CircleHelp size={15} /></button>
      {position && createPortal(
        <span role="tooltip" id={id} className="help-bubble" style={{
          left: position.left, top: position.top,
          transform: position.above ? "translateY(-100%)" : undefined,
        }}>{children}</span>,
        document.body,
      )}
    </span>
  );
}

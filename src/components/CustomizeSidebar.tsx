import { useEffect, useState } from "react";
import {
  SIDEBAR_ITEMS,
  getSidebarPrefs,
  setSidebarPrefs,
  type SidebarItem,
} from "../lib/sidebar";

/** Small modal that lets the user choose which sidebar items appear. */
export default function CustomizeSidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}): JSX.Element | null {
  const [visible, setVisible] = useState(() => getSidebarPrefs().visible);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const toggle = (id: SidebarItem, next: boolean): void => {
    const updated = setSidebarPrefs({ [id]: next });
    setVisible(updated.visible);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl material-overlay border border-bd/[0.1] shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3 border-b border-bd/[0.06]">
          <div>
            <div className="text-[15px] font-semibold text-tx">Customize sidebar</div>
            <div className="text-[12px] text-tx-mut mt-0.5">
              Choose which items appear in your sidebar.
            </div>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-tx-mut hover:text-tx rounded-lg p-1 hover:bg-bd/[0.06]"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-5 py-3 space-y-1">
          {SIDEBAR_ITEMS.map((item) => {
            const on = !!visible[item.id];
            return (
              <label
                key={item.id}
                className="flex items-center gap-3 px-2 py-2 rounded-lg cursor-pointer hover:bg-bd/[0.04]"
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => toggle(item.id, e.target.checked)}
                  className="w-4 h-4 accent-[color:var(--accent)]"
                />
                <span className="flex-1">
                  <span className="text-[13.5px] text-tx">{item.label}</span>
                  <span className="block text-[11.5px] text-tx-mut mt-0.5">
                    {item.hint}
                  </span>
                </span>
              </label>
            );
          })}
        </div>

        <div className="px-5 py-3 border-t border-bd/[0.06] flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-accent hover:bg-accent-hover text-black font-medium px-3.5 py-1.5 text-[13px] transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

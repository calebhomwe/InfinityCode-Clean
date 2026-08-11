import type { MouseEvent } from "react";

/**
 * Invisible resize handles for the undecorated Tauri window.
 *
 * With decorations:false the native frame (and its resize borders) is gone,
 * so the window cannot be resized by dragging its edges. These eight zones
 * (4 edges + 4 corners, 6px hit area) call the Tauri v1 startResizeDragging
 * API via a small Rust command (tauri v1 has no JS startResizeDragging),
 * restoring the standard desktop feel. Mounted at the shell root so the
 * window is resizable on every screen including the splash. No-ops in a plain
 * browser (__TAURI_IPC__ absent), where the OS window manager owns resizing.
 */
type ResizeDirection =
  | "East"
  | "North"
  | "NorthEast"
  | "NorthWest"
  | "South"
  | "SouthEast"
  | "SouthWest"
  | "West";

const EDGES: { dir: ResizeDirection; className: string }[] = [
  { dir: "North", className: "wr-handle wr-n" },
  { dir: "South", className: "wr-handle wr-s" },
  { dir: "East", className: "wr-handle wr-e" },
  { dir: "West", className: "wr-handle wr-w" },
  { dir: "NorthEast", className: "wr-handle wr-ne" },
  { dir: "NorthWest", className: "wr-handle wr-nw" },
  { dir: "SouthEast", className: "wr-handle wr-se" },
  { dir: "SouthWest", className: "wr-handle wr-sw" },
];

export default function WindowResizeHandles(): JSX.Element | null {
  const inTauri = "__TAURI_IPC__" in window;
  if (!inTauri) {
    return null;
  }

  const startResize = (dir: ResizeDirection) => (e: MouseEvent<HTMLDivElement>): void => {
    e.preventDefault();
    e.stopPropagation();
    // Tauri v1 has no JS startResizeDragging; the Rust command owns the call.
    void import("@tauri-apps/api/tauri")
      .then(({ invoke }) => invoke("start_resize", { direction: dir }))
      .catch(() => undefined);
  };

  return (
    <div className="wr-layer" aria-hidden="true">
      {EDGES.map(({ dir, className }) => (
        <div key={dir} className={className} onMouseDown={startResize(dir)} />
      ))}
    </div>
  );
}

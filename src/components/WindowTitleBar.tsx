import { invoke } from "@tauri-apps/api/tauri";
import { Minus, Square, X } from "lucide-react";

interface WindowTitleBarProps {
  title: string;
}

/**
 * Compact, platform-neutral title bar used by the desktop shell.
 * The window keeps the native snap/resize behaviour while the content below
 * owns the quiet charcoal chrome from the reference screen.
 */
export default function WindowTitleBar({ title }: WindowTitleBarProps): JSX.Element {
  // Use custom Rust commands: the bundled Tauri 1.8 window plugin cannot
  // resolve the window label ("plugin window not found"), so the title
  // bar drives win_close / win_toggle_maximize / win_minimize - the same
  // invoke path start_resize already proves works. In a plain browser
  // these reject harmlessly; the try/catch keeps preview builds quiet.
  const minimize = (): void => {
    void invoke("win_minimize").catch(() => undefined);
  };

  const toggleMaximize = (): void => {
    void invoke("win_toggle_maximize").catch(() => undefined);
  };

  const close = (): void => {
    void invoke("win_close").catch(() => undefined);
  };

  // Stop the mousedown from reaching the header drag region so the
  // buttons always receive their click.
  const stop = (e: React.MouseEvent): void => e.stopPropagation();

  // The plugin drag region (data-tauri-drag-region) dies on the same
  // broken window plugin as the old buttons, so drive the OS move loop
  // through the proven invoke path instead. Buttons stopPropagation on
  // mousedown, so they never trigger a drag.
  const startDrag = (e: React.MouseEvent): void => {
    if (e.button === 0) {
      void invoke("win_start_drag").catch(() => undefined);
    }
  };

  return (
    <header
      className="window-titlebar"
      onDoubleClick={toggleMaximize}
      onMouseDown={startDrag}
    >
      <span className="window-titlebar__title">{title}</span>
      <div className="window-titlebar__controls" aria-label="Window controls">
        <button type="button" className="window-control" onClick={minimize} onMouseDown={stop} aria-label="Minimize">
          <Minus aria-hidden="true" />
        </button>
        <button type="button" className="window-control" onClick={toggleMaximize} onMouseDown={stop} aria-label="Maximize">
          <Square aria-hidden="true" />
        </button>
        <button type="button" className="window-control window-control--close" onClick={close} onMouseDown={stop} aria-label="Close">
          <X aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}

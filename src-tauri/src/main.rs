#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{
    AppHandle, CustomMenuItem, Manager, RunEvent, SystemTray, SystemTrayEvent,
    SystemTrayMenu, SystemTrayMenuItem, WindowEvent,
};

const BACKEND_ADDR: &str = "127.0.0.1:8000";
const READY_TIMEOUT_SECS: u64 = 120;
const POLL_INTERVAL_MS: u64 = 500;
// Single-instance guard: the first instance binds this port and listens for
// "show yourself" pings; later launches ping it and exit instead of opening
// a duplicate window (and a duplicate backend).
const INSTANCE_LOCK_ADDR: &str = "127.0.0.1:48213";

/// Handle to the spawned Python backend so we can kill it on exit.
struct BackendProcess(Mutex<Option<Child>>);

/// Where mutable data (skills, missions.db, outputs) lives for this run.
struct DataDir(PathBuf);

/// False until the backend opens port 8000. A close request before then must
/// QUIT (hiding to tray would strand the user on a stuck splash with no way
/// out); after readiness, closing hides to tray as designed.
struct BackendReady(Mutex<bool>);

fn backend_is_up() -> bool {
    let addr: SocketAddr = match BACKEND_ADDR.parse() {
        Ok(addr) => addr,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// Repo root when running `tauri dev` (compile-time path of src-tauri/..).
fn dev_repo_root() -> Option<PathBuf> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|p| p.to_path_buf())
}

/// Pick the data directory: OS app-data dir in production, backend/ in dev.
fn resolve_data_dir(handle: &AppHandle) -> PathBuf {
    if cfg!(debug_assertions) {
        if let Some(root) = dev_repo_root() {
            let dev_backend = root.join("backend");
            if dev_backend.exists() {
                return dev_backend;
            }
        }
    }
    handle
        .path_resolver()
        .app_data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Locate the backend and build the command to launch it.
/// Order: bundled resource exe -> exe-adjacent exe -> dev venv uvicorn.
fn backend_command(handle: &AppHandle, data_dir: &PathBuf) -> Option<Command> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Some(resource) = handle
        .path_resolver()
        .resolve_resource("binaries/infinity-backend.exe")
    {
        candidates.push(resource);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("infinity-backend.exe"));
        }
    }

    for candidate in candidates {
        if candidate.exists() {
            let mut cmd = Command::new(candidate);
            cmd.env("INFINITY_DATA_DIR", data_dir);
            return Some(cmd);
        }
    }

    // Dev fallback: venv python + uvicorn against the repo backend.
    let root = dev_repo_root()?;
    let python = root.join("venv").join("Scripts").join("python.exe");
    let backend_dir = root.join("backend");
    if python.exists() && backend_dir.exists() {
        let mut cmd = Command::new(python);
        cmd.args(["-m", "uvicorn", "main:app", "--port", "8000", "--app-dir"]);
        cmd.arg(&backend_dir);
        cmd.env("INFINITY_DATA_DIR", data_dir);
        return Some(cmd);
    }
    None
}

#[cfg(windows)]
fn hide_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_cmd: &mut Command) {}

/// Spawn the backend (unless one is already listening) and emit
/// `backend-ready` / `backend-failed` to the frontend when resolved.
fn start_backend(handle: AppHandle) {
    std::thread::spawn(move || {
        if !backend_is_up() {
            let data_dir = {
                let state = handle.state::<DataDir>();
                state.0.clone()
            };
            match backend_command(&handle, &data_dir) {
                Some(mut cmd) => {
                    hide_console(&mut cmd);
                    match cmd.spawn() {
                        Ok(child) => {
                            let state = handle.state::<BackendProcess>();
                            *state.0.lock().expect("backend mutex poisoned") = Some(child);
                        }
                        Err(err) => {
                            eprintln!("Failed to spawn backend: {err}");
                            let _ = handle.emit_all("backend-failed", err.to_string());
                            return;
                        }
                    }
                }
                None => {
                    let message = "No backend found (bundled exe or dev venv).";
                    eprintln!("{message}");
                    let _ = handle.emit_all("backend-failed", message.to_string());
                    return;
                }
            }
        }

        let deadline = Instant::now() + Duration::from_secs(READY_TIMEOUT_SECS);
        while Instant::now() < deadline {
            if backend_is_up() {
                {
                    let state = handle.state::<BackendReady>();
                    *state.0.lock().expect("ready mutex poisoned") = true;
                }
                // The webview may still be loading its JS bundle when the
                // first emit fires, so repeat for a while — the splash
                // unsubscribes after the first one it hears.
                for _ in 0..15 {
                    let _ = handle.emit_all("backend-ready", ());
                    std::thread::sleep(Duration::from_secs(1));
                }
                return;
            }
            std::thread::sleep(Duration::from_millis(POLL_INTERVAL_MS));
        }
        let _ = handle.emit_all(
            "backend-failed",
            format!("Backend did not open port 8000 within {READY_TIMEOUT_SECS}s."),
        );
    });
}

/// JS-invokable resize: the undecorated window has no native frame, so the
/// frontend's invisible edge/corner zones drive the OS resize loop.
///
/// Tauri 1.8 has no start_resize_dragging API, so on Windows we use the
/// standard frameless-window technique: send WM_NCLBUTTONDOWN with the
/// non-client hit-test code for the requested edge/corner. Windows then runs
/// its native modal resize loop (smooth, DPI-aware) until mouse-up.
#[cfg(windows)]
#[tauri::command]
fn start_resize(window: tauri::Window, direction: String) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{SendMessageW, WM_NCLBUTTONDOWN};
    // Non-client hit-test codes (HTLEFT..HTBOTTOMRIGHT).
    let ht: isize = match direction.as_str() {
        "North" => 12,
        "South" => 15,
        "East" => 11,
        "West" => 10,
        "NorthEast" => 14,
        "NorthWest" => 13,
        "SouthEast" => 17,
        "SouthWest" => 16,
        other => return Err(format!("unknown resize direction: {other}")),
    };
    let hwnd = window.hwnd().map_err(|err| err.to_string())?;
    unsafe {
        SendMessageW(hwnd.0, WM_NCLBUTTONDOWN, ht as usize, 0);
    }
    Ok(())
}

#[cfg(not(windows))]
#[tauri::command]
fn start_resize(window: tauri::Window, _direction: String) -> Result<(), String> {
    // Non-Windows builds keep the command so the frontend invoke never fails;
    // native frames there already provide their own resize borders.
    let _ = window;
    Ok(())
}

/// Window-control commands. The plugin:window|* commands cannot resolve
/// the window label on Tauri 1.8 ("plugin window not found"), so these
/// use the magic `window: tauri::Window` injection like start_resize -
/// the one invoke path proven to work in this build.
#[tauri::command]
fn win_close(window: tauri::Window) {
    let _ = window.close();
}

#[tauri::command]
fn win_toggle_maximize(window: tauri::Window) -> Result<(), String> {
    if window.is_maximized().map_err(|e| e.to_string())? {
        window.unmaximize().map_err(|e| e.to_string())
    } else {
        window.maximize().map_err(|e| e.to_string())
    }
}

#[tauri::command]
fn win_minimize(window: tauri::Window) {
    let _ = window.minimize();
}

/// Title-bar drag. The JS window plugin cannot resolve the window label in
/// this build, so the title bar invokes this Rust command instead. Tauri's
/// core start_dragging (tao's drag_window) does ReleaseCapture followed by
/// WM_NCLBUTTONDOWN + HTCAPTION; the earlier hand-rolled SendMessage skipped
/// ReleaseCapture, so WebView2 kept mouse capture and the OS move loop never
/// tracked the mouse.
#[tauri::command]
fn win_start_drag(window: tauri::Window) -> Result<(), String> {
    window.start_dragging().map_err(|err| err.to_string())
}

fn kill_backend(handle: &AppHandle) {
    let state = handle.state::<BackendProcess>();
    // Take the child out in its own statement so the MutexGuard temporary
    // drops before `state` does (E0597 on tail-position if-let otherwise).
    let child = state.0.lock().ok().and_then(|mut guard| guard.take());
    if let Some(mut child) = child {
        // PyInstaller onefile is a bootstrap that spawns the real server as
        // its own child; killing only the parent orphans the server and
        // leaves port 8000 held. Tree-kill on Windows.
        #[cfg(windows)]
        {
            let mut cmd = Command::new("taskkill");
            cmd.args(["/PID", &child.id().to_string(), "/T", "/F"]);
            hide_console(&mut cmd);
            let _ = cmd.status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn open_vault_folder(handle: &AppHandle) {
    let state = handle.state::<DataDir>();
    let vault = state.0.join("skills");
    if let Err(err) = std::fs::create_dir_all(&vault) {
        eprintln!("Could not create vault folder {vault:?}: {err}");
        return;
    }
    #[cfg(windows)]
    {
        let mut cmd = Command::new("explorer");
        cmd.arg(&vault);
        if let Err(err) = cmd.spawn() {
            eprintln!("Could not open vault folder: {err}");
        }
    }
    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        if let Err(err) = Command::new(opener).arg(&vault).spawn() {
            eprintln!("Could not open vault folder: {err}");
        }
    }
}

/// Bind the instance-lock port. None means another instance is already
/// running (we ping it so it fronts its window, and the caller should exit).
fn claim_single_instance() -> Option<TcpListener> {
    match TcpListener::bind(INSTANCE_LOCK_ADDR) {
        Ok(listener) => Some(listener),
        Err(_) => {
            if let Ok(addr) = INSTANCE_LOCK_ADDR.parse::<SocketAddr>() {
                let _ = TcpStream::connect_timeout(&addr, Duration::from_millis(300));
            }
            None
        }
    }
}

/// Accept pings from later launches and front the main window for each one.
fn serve_instance_lock(handle: AppHandle, listener: TcpListener) {
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            if stream.is_ok() {
                if let Some(window) = handle.get_window("main") {
                    let _ = window.show();
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                }
            }
        }
    });
}

fn build_tray() -> SystemTray {
    let menu = SystemTrayMenu::new()
        .add_item(CustomMenuItem::new("show", "Show"))
        .add_item(CustomMenuItem::new("hide", "Hide"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("vault", "Open Vault Folder"))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("quit", "Quit"));
    SystemTray::new().with_menu(menu)
}

fn main() {
    let instance_lock = match claim_single_instance() {
        Some(listener) => listener,
        None => return, // another instance is running and has been fronted
    };

    let app = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .manage(BackendReady(Mutex::new(false)))
        .system_tray(build_tray())
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::LeftClick { .. } => {
                if let Some(window) = app.get_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
                "show" => {
                    if let Some(window) = app.get_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                "hide" => {
                    if let Some(window) = app.get_window("main") {
                        let _ = window.hide();
                    }
                }
                "vault" => open_vault_folder(app),
                "quit" => {
                    kill_backend(app);
                    app.exit(0);
                }
                _ => {}
            },
            _ => {}
        })
        .on_window_event(|event| {
            // X (or any close request) always exits the app for real:
            // kill the backend and exit explicitly, so a tray-held event
            // loop can never keep the app alive after the user closes it.
            if let WindowEvent::CloseRequested { .. } = event.event() {
                let handle = event.window().app_handle();
                kill_backend(&handle);
                handle.exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_resize,
            win_close,
            win_toggle_maximize,
            win_minimize,
            win_start_drag,
        ])
        .setup(move |app| {
            let data_dir = resolve_data_dir(&app.handle());
            std::fs::create_dir_all(&data_dir).ok();
            app.manage(DataDir(data_dir));
            serve_instance_lock(app.handle(), instance_lock);
            start_backend(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            kill_backend(handle);
        }
    });
}

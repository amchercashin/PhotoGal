use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{Manager, State};

mod cuda;

pub struct BackendProcess(Mutex<Option<Child>>);

/// Find a free TCP port
fn find_free_port() -> u16 {
    use std::net::TcpListener;
    TcpListener::bind("127.0.0.1:0")
        .expect("failed to bind")
        .local_addr()
        .unwrap()
        .port()
}

/// Path to sidecar log file
fn sidecar_log_path() -> std::path::PathBuf {
    let base = dirs::data_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    #[cfg(target_os = "macos")]
    let dir = base.join("com.photogal.desktop");
    #[cfg(target_os = "windows")]
    let dir = base.join("PhotoGal");
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let dir = base.join("photogal");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("sidecar.log")
}

/// Wait for backend to be ready by polling /api/health.
/// Returns `true` if backend responded with 200, `false` on timeout.
pub fn wait_for_backend(port: u16, timeout_ms: u64) -> bool {
    let url = format!("http://127.0.0.1:{}/api/health", port);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(timeout_ms);
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_secs(2))
        .timeout_read(std::time::Duration::from_secs(5))
        .build();
    loop {
        if std::time::Instant::now() > deadline {
            eprintln!("Backend did not start within {}ms", timeout_ms);
            return false;
        }
        if let Ok(resp) = agent.get(&url).call() {
            if resp.status() == 200 {
                eprintln!("Backend ready on port {}", port);
                return true;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
}

pub fn start_sidecar(
    sidecar_bin: &std::path::Path,
    port: u16,
) -> Result<Child, String> {
    let sidecar_dir = sidecar_bin.parent().unwrap().to_path_buf();
    let log_path = sidecar_log_path();
    eprintln!("Sidecar log: {:?}", log_path);

    let log_file = std::fs::File::create(&log_path)
        .map_err(|e| format!("Failed to create sidecar log: {e}"))?;
    let stderr_file = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone log file handle: {e}"))?;

    let mut cmd = Command::new(sidecar_bin);
    cmd.current_dir(&sidecar_dir)
        .args(["serve", "--port", &port.to_string()])
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(stderr_file));
    // Hide console window on Windows
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    cmd.spawn()
        .map_err(|e| format!("Failed to start sidecar: {e}"))
}

/// Find the sidecar binary path in bundled Resources or dev sidecar/ dir
pub fn find_sidecar_path(handle: &impl tauri::Manager<tauri::Wry>) -> Option<std::path::PathBuf> {
    let bin_name = format!("photogal-server-bin{}", std::env::consts::EXE_SUFFIX);
    // Production: bundled in Resources/sidecar/
    if let Ok(resource_dir) = handle.path().resource_dir() {
        let bin = resource_dir.join("sidecar").join(&bin_name);
        if bin.exists() {
            return Some(bin);
        }
        // Flat layout: Tauri may flatten resources into resource_dir root
        let bin = resource_dir.join(&bin_name);
        if bin.exists() {
            return Some(bin);
        }
    }
    // Dev: sidecar/ directory next to src-tauri
    let dev_bin = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("sidecar")
        .join(&bin_name);
    if dev_bin.exists() {
        return Some(dev_bin);
    }
    None
}

#[tauri::command]
fn get_backend_port(state: State<'_, Mutex<u16>>) -> u16 {
    *state.lock().unwrap()
}

#[tauri::command]
fn get_sidecar_status(state: State<'_, BackendProcess>) -> serde_json::Value {
    let mut guard = state.0.lock().unwrap();
    match guard.as_mut() {
        None => serde_json::json!({ "status": "not_started" }),
        Some(child) => match child.try_wait() {
            Ok(Some(exit_status)) => {
                serde_json::json!({
                    "status": "exited",
                    "exit_code": exit_status.code()
                })
            }
            Ok(None) => serde_json::json!({ "status": "running" }),
            Err(e) => serde_json::json!({
                "status": "error",
                "error": format!("{e}")
            }),
        },
    }
}

#[tauri::command]
fn get_sidecar_log_path() -> String {
    sidecar_log_path().to_string_lossy().into_owned()
}

#[tauri::command]
fn reveal_in_finder(path: String) -> Result<(), String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("File not found: {}", path));
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("-R")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to reveal in Finder: {}", e))?;
    }
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer.exe")
            .arg(format!("/select,{}", &path))
            .spawn()
            .map_err(|e| format!("Failed to reveal in Explorer: {}", e))?;
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = find_free_port();
    let port_copy = port;

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendProcess(Mutex::new(None)))
        .manage(Mutex::new(port))
        .setup(move |app| {
            if let Some(sidecar_bin) = find_sidecar_path(app) {
                eprintln!("Launching sidecar: {:?}", sidecar_bin);
                match start_sidecar(&sidecar_bin, port_copy) {
                    Ok(child) => {
                        let state = app.state::<BackendProcess>();
                        *state.0.lock().unwrap() = Some(child);
                        let p = port_copy;
                        std::thread::spawn(move || wait_for_backend(p, 60_000));
                    }
                    Err(e) => {
                        eprintln!("{e}");
                    }
                }
            } else {
                eprintln!("Sidecar not found — assuming dev mode (backend runs separately)");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.try_state::<BackendProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_backend_port,
            get_sidecar_status,
            get_sidecar_log_path,
            reveal_in_finder,
            cuda::download_cuda_addon,
            cuda::check_cuda_status,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

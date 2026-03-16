use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, State};

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

/// Wait for backend to be ready by polling /api/health
fn wait_for_backend(port: u16, timeout_ms: u64) {
    let url = format!("http://127.0.0.1:{}/api/health", port);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(timeout_ms);
    loop {
        if std::time::Instant::now() > deadline {
            eprintln!("Backend did not start within {}ms", timeout_ms);
            break;
        }
        if let Ok(resp) = ureq::get(&url).call() {
            if resp.status() == 200 {
                eprintln!("Backend ready on port {}", port);
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
}

/// Find the sidecar binary path in bundled Resources or dev sidecar/ dir
fn find_sidecar_path(app: &tauri::App) -> Option<std::path::PathBuf> {
    let bin_name = format!("photogal-server-bin{}", std::env::consts::EXE_SUFFIX);
    // Production: bundled in Resources/sidecar/
    if let Ok(resource_dir) = app.path().resource_dir() {
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
                let sidecar_dir = sidecar_bin.parent().unwrap().to_path_buf();
                match Command::new(&sidecar_bin)
                    .current_dir(&sidecar_dir)
                    .args(["serve", "--port", &port_copy.to_string()])
                    .spawn()
                {
                    Ok(child) => {
                        let state = app.state::<BackendProcess>();
                        *state.0.lock().unwrap() = Some(child);
                        let p = port_copy;
                        std::thread::spawn(move || wait_for_backend(p, 60_000));
                    }
                    Err(e) => {
                        eprintln!("Failed to start sidecar: {e}");
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
        .invoke_handler(tauri::generate_handler![get_backend_port, reveal_in_finder])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

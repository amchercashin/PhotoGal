use std::sync::Mutex;
use tauri::{Manager, State};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

pub struct BackendProcess(Mutex<Option<CommandChild>>);

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
        std::process::Command::new("open")
            .arg("-R")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to reveal in Finder: {}", e))?;
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
            // Select GPU sidecar if available, otherwise CPU sidecar
            let gpu_sidecar_path = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| dirs::home_dir().unwrap_or_default().join(".photogal"))
                .join("sidecars")
                .join("photogal-server-gpu");
            let sidecar_name = if gpu_sidecar_path.exists() {
                eprintln!("Using GPU sidecar");
                "photogal-server-gpu"
            } else {
                "photogal-server"
            };

            let sidecar_result = app
                .shell()
                .sidecar(sidecar_name)
                .map(|cmd| cmd.args(["serve", "--port", &port_copy.to_string()]))
                .and_then(|cmd| cmd.spawn());

            match sidecar_result {
                Ok((mut rx, child)) => {
                    let state = app.state::<BackendProcess>();
                    *state.0.lock().unwrap() = Some(child);

                    let p = port_copy;
                    std::thread::spawn(move || wait_for_backend(p, 60_000));

                    // Drain sidecar output
                    tauri::async_runtime::spawn(async move {
                        use tauri_plugin_shell::process::CommandEvent;
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(line) => {
                                    eprintln!("[backend] {}", String::from_utf8_lossy(&line))
                                }
                                CommandEvent::Stderr(line) => {
                                    eprintln!("[backend:err] {}", String::from_utf8_lossy(&line))
                                }
                                CommandEvent::Terminated(_) => break,
                                _ => {}
                            }
                        }
                    });
                }
                Err(e) => {
                    eprintln!("Failed to start backend sidecar: {e}");
                    // In dev mode, backend runs separately — continue anyway
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.try_state::<BackendProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() {
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

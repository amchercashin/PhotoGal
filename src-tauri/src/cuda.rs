use std::fs;
use std::io::{Read, Write};
use std::path::Path;
use std::process::Child;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager};

use crate::{BackendProcess, find_sidecar_path, start_sidecar, wait_for_backend};

/// Check if CUDA sidecar is installed
#[tauri::command]
pub fn check_cuda_status(handle: AppHandle) -> Result<serde_json::Value, String> {
    if let Some(sidecar_bin) = find_sidecar_path(&handle) {
        let marker = sidecar_bin.parent().unwrap().join("cuda_installed");
        if marker.exists() {
            return Ok(serde_json::json!({ "installed": true }));
        }
    }
    Ok(serde_json::json!({ "installed": false }))
}

/// Stop sidecar process, killing entire process tree on Windows
pub(crate) fn stop_sidecar(child: &mut Child) {
    #[cfg(target_os = "windows")]
    {
        // Kill entire process tree on Windows to release all file handles.
        // child.kill() only terminates the parent; PyInstaller may spawn
        // multiprocessing workers that keep DLLs locked.
        let pid = child.id();
        let _ = std::process::Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = child.kill();
    }
    let _ = child.wait();
}

/// Rename directory with retry — handles delayed file handle release on Windows
fn rename_with_retry(from: &Path, to: &Path, context: &str) -> Result<(), String> {
    let max_attempts = 5;
    for attempt in 1..=max_attempts {
        match fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e) => {
                if attempt == max_attempts {
                    return Err(format!("Failed to {context}: {e}"));
                }
                eprintln!(
                    "{context}: rename attempt {}/{} failed: {e}, retrying in 2s...",
                    attempt, max_attempts
                );
                std::thread::sleep(Duration::from_secs(2));
            }
        }
    }
    unreachable!()
}

/// Download CUDA sidecar from GitHub Releases and swap it in.
/// Runs on a background thread to keep the UI responsive.
#[tauri::command]
pub async fn download_cuda_addon(handle: AppHandle) -> Result<String, String> {
    let handle2 = handle.clone();
    tauri::async_runtime::spawn_blocking(move || {
        do_download_cuda(handle2)
    })
    .await
    .map_err(|e| format!("Internal error: {e}"))?
}

fn do_download_cuda(handle: AppHandle) -> Result<String, String> {
    let version = handle.package_info().version.to_string();
    let url = format!(
        "https://github.com/amchercashin/PhotoGal/releases/download/v{}/PhotoGal_{}_cuda-sidecar.7z",
        version, version
    );

    let sidecar_bin = find_sidecar_path(&handle)
        .ok_or("Sidecar not found — cannot install CUDA addon")?;
    let sidecar_dir = sidecar_bin.parent().unwrap().to_path_buf();

    let port = {
        let state = handle.state::<std::sync::Mutex<u16>>();
        let val = *state.lock().unwrap_or_else(|e| e.into_inner());
        val
    };

    let temp_dir = std::env::temp_dir();
    let archive_path = temp_dir.join("photogal-cuda.7z");
    let extract_dir = temp_dir.join("photogal-cuda-sidecar");

    // --- Download with retry + resume ---
    eprintln!("Downloading CUDA sidecar from {}", url);

    let agent = ureq::AgentBuilder::new()
        .timeout_read(Duration::from_secs(30))
        .timeout_connect(Duration::from_secs(15))
        .build();

    let max_retries = 3;
    let mut downloaded: u64 = 0;
    let mut total_bytes: Option<u64> = None;

    for attempt in 0..max_retries {
        let resp = if downloaded > 0 {
            eprintln!("Retry {}/{}: resuming from {} bytes", attempt + 1, max_retries, downloaded);
            match agent.get(&url)
                .set("Range", &format!("bytes={}-", downloaded))
                .call()
            {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Connection error (attempt {}): {e}", attempt + 1);
                    if attempt == max_retries - 1 {
                        return Err(format!("Download failed after {} attempts: {e}", max_retries));
                    }
                    std::thread::sleep(Duration::from_secs(2));
                    continue;
                }
            }
        } else {
            match agent.get(&url).call() {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Connection error (attempt {}): {e}", attempt + 1);
                    if attempt == max_retries - 1 {
                        return Err(format!("Download failed after {} attempts: {e}", max_retries));
                    }
                    std::thread::sleep(Duration::from_secs(2));
                    continue;
                }
            }
        };

        // If resume requested but server doesn't support it, start over
        if downloaded > 0 && resp.status() != 206 {
            eprintln!("Server doesn't support resume, starting over");
            downloaded = 0;
        }

        // Parse total size from first successful response
        if total_bytes.is_none() {
            if let Some(cl) = resp.header("Content-Length") {
                if let Ok(len) = cl.parse::<u64>() {
                    total_bytes = Some(if resp.status() == 206 { downloaded + len } else { len });
                    let _ = handle.emit("cuda-download-progress", serde_json::json!({
                        "downloaded_mb": downloaded as f64 / 1_048_576.0,
                        "total_mb": total_bytes.unwrap() as f64 / 1_048_576.0
                    }));
                }
            }
        }

        // Open file: create new or append for resume
        let mut file = if downloaded > 0 {
            fs::OpenOptions::new()
                .append(true)
                .open(&archive_path)
                .map_err(|e| format!("Failed to open file for resume: {e}"))?
        } else {
            fs::File::create(&archive_path)
                .map_err(|e| format!("Failed to create temp file: {e}"))?
        };

        let mut body = resp.into_reader();
        let mut buf = [0u8; 262_144]; // 256KB buffer
        let mut last_emit = Instant::now();
        let mut read_error = false;

        loop {
            match body.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    file.write_all(&buf[..n]).map_err(|e| format!("Write error: {e}"))?;
                    downloaded += n as u64;

                    // Throttle events: emit at most every 250ms
                    if last_emit.elapsed() >= Duration::from_millis(250) {
                        let _ = handle.emit("cuda-download-progress", serde_json::json!({
                            "downloaded_mb": downloaded as f64 / 1_048_576.0,
                            "total_mb": total_bytes.map(|t| t as f64 / 1_048_576.0)
                        }));
                        last_emit = Instant::now();
                    }
                }
                Err(e) => {
                    eprintln!("Read error at {} bytes: {e}", downloaded);
                    read_error = true;
                    break;
                }
            }
        }

        if !read_error {
            // Final progress event
            let _ = handle.emit("cuda-download-progress", serde_json::json!({
                "downloaded_mb": downloaded as f64 / 1_048_576.0,
                "total_mb": total_bytes.map(|t| t as f64 / 1_048_576.0)
            }));
            break;
        }

        if attempt == max_retries - 1 {
            return Err(format!(
                "Download failed after {} attempts at {} MB",
                max_retries,
                downloaded / 1_048_576
            ));
        }

        std::thread::sleep(Duration::from_secs(2));
    }

    eprintln!("Download complete: {} MB", downloaded / 1_048_576);

    // --- Extract 7z ---
    let _ = handle.emit("cuda-download-progress", serde_json::json!({
        "stage": "extracting",
        "downloaded_mb": downloaded as f64 / 1_048_576.0,
        "total_mb": total_bytes.map(|t| t as f64 / 1_048_576.0)
    }));
    eprintln!("Extracting to {:?}", extract_dir);
    if extract_dir.exists() {
        let _ = fs::remove_dir_all(&extract_dir);
    }
    fs::create_dir_all(&extract_dir)
        .map_err(|e| format!("Failed to create extract dir: {e}"))?;

    sevenz_rust::decompress_file(&archive_path, &extract_dir)
        .map_err(|e| format!("Failed to extract 7z archive: {e}"))?;

    // --- Safe swap ---
    let _ = handle.emit("cuda-download-progress", serde_json::json!({
        "stage": "installing"
    }));
    let backup_dir = sidecar_dir.with_file_name("sidecar-cpu-backup");

    // Stop running sidecar — kill entire process tree
    eprintln!("Stopping backend for CUDA swap...");
    {
        let backend_state = handle.state::<BackendProcess>();
        let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(ref mut child) = *guard {
            stop_sidecar(child);
        }
        let _ = guard.take();
    }
    // Extra delay for OS to release file handles (critical on Windows)
    std::thread::sleep(Duration::from_secs(2));

    if backup_dir.exists() {
        let _ = fs::remove_dir_all(&backup_dir);
    }

    // Rename with retry — Windows may delay releasing file locks
    rename_with_retry(&sidecar_dir, &backup_dir, "backup sidecar")?;
    if let Err(e) = rename_with_retry(&extract_dir, &sidecar_dir, "install CUDA sidecar") {
        let _ = rename_with_retry(&backup_dir, &sidecar_dir, "rollback sidecar");
        return Err(e);
    }

    let marker = sidecar_dir.join("cuda_installed");
    let _ = fs::write(&marker, "1");

    // Start new sidecar
    let bin_name = format!("photogal-server-bin{}", std::env::consts::EXE_SUFFIX);
    let new_bin = sidecar_dir.join(&bin_name);
    match start_sidecar(&new_bin, port) {
        Ok(child) => {
            {
                let backend_state = handle.state::<BackendProcess>();
                let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
                *guard = Some(child);
            }

            // Brief delay to catch immediate crashes (e.g. DLL load failure)
            std::thread::sleep(Duration::from_secs(2));

            let mut crashed = false;
            {
                let backend_state = handle.state::<BackendProcess>();
                let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
                if let Some(ref mut c) = *guard {
                    if let Ok(Some(exit)) = c.try_wait() {
                        eprintln!("CUDA sidecar exited immediately with: {:?}", exit.code());
                        crashed = true;
                    }
                }
            }

            // Short timeout: CUDA sidecar either starts fast or not at all.
            let backend_ok = !crashed && wait_for_backend(port, 15_000);

            if backend_ok {
                let _ = fs::remove_dir_all(&backup_dir);
                let _ = fs::remove_file(&archive_path);
                eprintln!("CUDA sidecar installed successfully");
                Ok("cuda".to_string())
            } else {
                // Preserve CUDA crash log before CPU restart overwrites it
                let log_path = crate::sidecar_log_path();
                let cuda_log = log_path.with_file_name("sidecar-cuda-crash.log");
                let crash_details = fs::read_to_string(&log_path).unwrap_or_default();
                let _ = fs::copy(&log_path, &cuda_log);
                eprintln!("CUDA sidecar failed health check, log saved to {:?}", cuda_log);
                if !crash_details.is_empty() {
                    eprintln!("CUDA sidecar log:\n{}", crash_details);
                }

                // Kill broken CUDA process
                {
                    let backend_state = handle.state::<BackendProcess>();
                    let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
                    if let Some(ref mut c) = *guard {
                        stop_sidecar(c);
                    }
                    let _ = guard.take();
                }
                std::thread::sleep(Duration::from_secs(2));

                // Restore CPU sidecar
                let _ = fs::remove_dir_all(&sidecar_dir);
                let _ = rename_with_retry(&backup_dir, &sidecar_dir, "restore CPU sidecar");
                let _ = fs::remove_file(sidecar_dir.join("cuda_installed"));

                // Restart CPU backend
                let cpu_bin = sidecar_dir.join(&bin_name);
                if let Ok(child) = start_sidecar(&cpu_bin, port) {
                    {
                        let backend_state = handle.state::<BackendProcess>();
                        let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
                        *guard = Some(child);
                    }
                    wait_for_backend(port, 30_000);
                }
                let _ = fs::remove_file(&archive_path);

                // Include crash log snippet in error for frontend display
                let snippet = if crash_details.len() > 200 {
                    format!("...{}", &crash_details[crash_details.len()-200..])
                } else {
                    crash_details
                };
                Err(format!("CUDA sidecar failed to start — restored CPU version.\nLog: {}", snippet))
            }
        }
        Err(e) => {
            eprintln!("CUDA sidecar failed to start, restoring CPU backup: {e}");
            let _ = fs::remove_dir_all(&sidecar_dir);
            let _ = rename_with_retry(&backup_dir, &sidecar_dir, "restore CPU sidecar");
            let _ = fs::remove_file(sidecar_dir.join("cuda_installed"));
            let cpu_bin = sidecar_dir.join(&bin_name);
            if let Ok(child) = start_sidecar(&cpu_bin, port) {
                {
                    let backend_state = handle.state::<BackendProcess>();
                    let mut guard = backend_state.0.lock().unwrap_or_else(|e| e.into_inner());
                    *guard = Some(child);
                }
                wait_for_backend(port, 60_000);
            }
            Err(format!("CUDA sidecar failed to start: {e}"))
        }
    }
}

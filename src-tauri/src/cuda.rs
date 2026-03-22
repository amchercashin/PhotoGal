use std::fs;
use std::io::{Read, Write};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, State};

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

/// Download CUDA sidecar from GitHub Releases and swap it in
#[tauri::command]
pub fn download_cuda_addon(
    handle: AppHandle,
    backend_state: State<'_, BackendProcess>,
    port_state: State<'_, Mutex<u16>>,
) -> Result<String, String> {
    let version = handle.package_info().version.to_string();
    let url = format!(
        "https://github.com/amchercashin/PhotoGal/releases/download/v{}/PhotoGal_{}_cuda-sidecar.7z",
        version, version
    );

    let sidecar_bin = find_sidecar_path(&handle)
        .ok_or("Sidecar not found — cannot install CUDA addon")?;
    let sidecar_dir = sidecar_bin.parent().unwrap().to_path_buf();

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
    eprintln!("Extracting to {:?}", extract_dir);
    if extract_dir.exists() {
        let _ = fs::remove_dir_all(&extract_dir);
    }
    fs::create_dir_all(&extract_dir)
        .map_err(|e| format!("Failed to create extract dir: {e}"))?;

    sevenz_rust::decompress_file(&archive_path, &extract_dir)
        .map_err(|e| format!("Failed to extract 7z archive: {e}"))?;

    // --- Safe swap ---
    let port = *port_state.lock().unwrap();
    let backup_dir = sidecar_dir.with_file_name("sidecar-cpu-backup");

    eprintln!("Stopping backend for CUDA swap...");
    if let Ok(mut guard) = backend_state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    std::thread::sleep(std::time::Duration::from_secs(1));

    if backup_dir.exists() {
        let _ = fs::remove_dir_all(&backup_dir);
    }
    fs::rename(&sidecar_dir, &backup_dir)
        .map_err(|e| format!("Failed to backup sidecar: {e}"))?;
    fs::rename(&extract_dir, &sidecar_dir)
        .map_err(|e| {
            let _ = fs::rename(&backup_dir, &sidecar_dir);
            format!("Failed to install CUDA sidecar: {e}")
        })?;

    let marker = sidecar_dir.join("cuda_installed");
    let _ = fs::write(&marker, "1");

    // Start new sidecar
    let bin_name = format!("photogal-server-bin{}", std::env::consts::EXE_SUFFIX);
    let new_bin = sidecar_dir.join(&bin_name);
    match start_sidecar(&new_bin, port) {
        Ok(child) => {
            if let Ok(mut guard) = backend_state.0.lock() {
                *guard = Some(child);
            }

            // Brief delay to catch immediate crashes (e.g. DLL load failure)
            std::thread::sleep(Duration::from_secs(2));

            let mut crashed = false;
            if let Ok(mut guard) = backend_state.0.lock() {
                if let Some(ref mut c) = *guard {
                    if let Ok(Some(exit)) = c.try_wait() {
                        eprintln!("CUDA sidecar exited immediately with: {:?}", exit.code());
                        crashed = true;
                    }
                }
            }

            // Short timeout: CUDA sidecar either starts fast or not at all.
            // Using 60s here would block the UI (sync Tauri command).
            let backend_ok = !crashed && wait_for_backend(port, 15_000);

            if backend_ok {
                let _ = fs::remove_dir_all(&backup_dir);
                let _ = fs::remove_file(&archive_path);
                eprintln!("CUDA sidecar installed successfully");
                Ok("cuda".to_string())
            } else {
                eprintln!("CUDA sidecar failed health check, restoring CPU backup");
                // Kill broken CUDA process
                if let Ok(mut guard) = backend_state.0.lock() {
                    if let Some(mut c) = guard.take() {
                        let _ = c.kill();
                        let _ = c.wait();
                    }
                }
                std::thread::sleep(Duration::from_secs(1));
                // Restore CPU sidecar
                let _ = fs::remove_dir_all(&sidecar_dir);
                let _ = fs::rename(&backup_dir, &sidecar_dir);
                let _ = fs::remove_file(sidecar_dir.join("cuda_installed"));
                // Restart CPU backend
                let cpu_bin = sidecar_dir.join(&bin_name);
                if let Ok(child) = start_sidecar(&cpu_bin, port) {
                    if let Ok(mut guard) = backend_state.0.lock() {
                        *guard = Some(child);
                    }
                    wait_for_backend(port, 30_000);
                }
                let _ = fs::remove_file(&archive_path);
                Err("CUDA sidecar failed to start — restored CPU version".to_string())
            }
        }
        Err(e) => {
            eprintln!("CUDA sidecar failed to start, restoring CPU backup: {e}");
            let _ = fs::remove_dir_all(&sidecar_dir);
            let _ = fs::rename(&backup_dir, &sidecar_dir);
            let _ = fs::remove_file(sidecar_dir.join("cuda_installed"));
            let cpu_bin = sidecar_dir.join(&bin_name);
            if let Ok(child) = start_sidecar(&cpu_bin, port) {
                if let Ok(mut guard) = backend_state.0.lock() {
                    *guard = Some(child);
                }
                wait_for_backend(port, 60_000);
            }
            Err(format!("CUDA sidecar failed to start: {e}"))
        }
    }
}

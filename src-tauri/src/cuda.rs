use std::fs;
use std::io::{Read, Write};
use std::sync::Mutex;

use tauri::{AppHandle, Emitter, State};

use crate::{BackendProcess, find_sidecar_path, start_sidecar, wait_for_backend};

/// Check if CUDA sidecar is installed
#[tauri::command]
pub fn check_cuda_status(handle: AppHandle) -> Result<serde_json::Value, String> {
    // Check marker file in sidecar directory
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
        "https://github.com/amchercashin/PhotoGal/releases/download/v{}/PhotoGal_{}_cuda-sidecar.zip",
        version, version
    );

    // Find current sidecar directory
    let sidecar_bin = find_sidecar_path(&handle)
        .ok_or("Sidecar not found — cannot install CUDA addon")?;
    let sidecar_dir = sidecar_bin.parent().unwrap().to_path_buf();

    // Temp paths
    let temp_dir = std::env::temp_dir();
    let zip_path = temp_dir.join("photogal-cuda.zip");
    let extract_dir = temp_dir.join("photogal-cuda-sidecar");

    // --- Download ---
    eprintln!("Downloading CUDA sidecar from {}", url);
    let resp = ureq::get(&url)
        .call()
        .map_err(|e| format!("Download failed: {e}"))?;

    let mut downloaded: u64 = 0;
    let mut body = resp.into_reader();
    let mut file = fs::File::create(&zip_path)
        .map_err(|e| format!("Failed to create temp file: {e}"))?;

    let mut buf = [0u8; 65536];
    loop {
        let n = body.read(&mut buf).map_err(|e| format!("Download error: {e}"))?;
        if n == 0 { break; }
        file.write_all(&buf[..n]).map_err(|e| format!("Write error: {e}"))?;
        downloaded += n as u64;
        let _ = handle.emit("cuda-download-progress", serde_json::json!({
            "downloaded_mb": downloaded as f64 / 1_048_576.0
        }));
    }
    drop(file);
    eprintln!("Download complete: {} MB", downloaded / 1_048_576);

    // --- Extract ---
    eprintln!("Extracting to {:?}", extract_dir);
    if extract_dir.exists() {
        let _ = fs::remove_dir_all(&extract_dir);
    }
    fs::create_dir_all(&extract_dir)
        .map_err(|e| format!("Failed to create extract dir: {e}"))?;

    let zip_file = fs::File::open(&zip_path)
        .map_err(|e| format!("Failed to open zip: {e}"))?;
    let mut archive = zip::ZipArchive::new(zip_file)
        .map_err(|e| format!("Invalid zip file: {e}"))?;

    for i in 0..archive.len() {
        let mut entry = archive.by_index(i)
            .map_err(|e| format!("Zip entry error: {e}"))?;
        let outpath = match entry.enclosed_name() {
            Some(name) => extract_dir.join(name),
            None => continue, // skip entries with path traversal
        };
        if entry.is_dir() {
            fs::create_dir_all(&outpath).ok();
        } else {
            if let Some(parent) = outpath.parent() {
                fs::create_dir_all(parent).ok();
            }
            let mut outfile = fs::File::create(&outpath)
                .map_err(|e| format!("Extract write error: {e}"))?;
            std::io::copy(&mut entry, &mut outfile)
                .map_err(|e| format!("Extract copy error: {e}"))?;
        }
    }

    // --- Safe swap ---
    let port = *port_state.lock().unwrap();
    let backup_dir = sidecar_dir.with_file_name("sidecar-cpu-backup");

    // Kill backend
    eprintln!("Stopping backend for CUDA swap...");
    if let Ok(mut guard) = backend_state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait(); // Wait for port release
        }
    }
    std::thread::sleep(std::time::Duration::from_secs(1));

    // Swap directories
    if backup_dir.exists() {
        let _ = fs::remove_dir_all(&backup_dir);
    }
    fs::rename(&sidecar_dir, &backup_dir)
        .map_err(|e| format!("Failed to backup sidecar: {e}"))?;
    fs::rename(&extract_dir, &sidecar_dir)
        .map_err(|e| {
            // Restore backup on failure
            let _ = fs::rename(&backup_dir, &sidecar_dir);
            format!("Failed to install CUDA sidecar: {e}")
        })?;

    // Write marker
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
            wait_for_backend(port, 60_000);
            // Cleanup
            let _ = fs::remove_dir_all(&backup_dir);
            let _ = fs::remove_file(&zip_path);
            eprintln!("CUDA sidecar installed successfully");
            Ok("cuda".to_string())
        }
        Err(e) => {
            // Restore backup
            eprintln!("CUDA sidecar failed to start, restoring CPU backup: {e}");
            let _ = fs::remove_dir_all(&sidecar_dir);
            let _ = fs::rename(&backup_dir, &sidecar_dir);
            let _ = fs::remove_file(&marker);
            // Restart CPU sidecar
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

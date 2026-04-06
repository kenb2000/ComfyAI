#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use reqwest::blocking::Client as BlockingClient;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Manager, State, Url, WebviewWindow};

#[cfg(windows)]
const WINDOWS_BACKEND_CREATION_FLAGS: u32 = 0x0000_0200;

#[derive(Default)]
struct ShellState {
    child: Mutex<Option<Child>>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LaunchResponse {
    url: String,
    reused_existing: bool,
}

#[derive(Debug, Default, Deserialize)]
struct Settings {
    #[serde(default)]
    tool_paths: ToolPaths,
    #[serde(default)]
    comfyui: ComfySettings,
}

#[derive(Debug, Default, Deserialize)]
struct ToolPaths {
    #[serde(default)]
    venv_dir: String,
    #[serde(default)]
    runtime_dir: String,
}

#[derive(Debug, Deserialize)]
struct ComfySettings {
    #[serde(default = "default_repo_path")]
    repo_path: String,
    #[serde(default)]
    python_executable: String,
    #[serde(default = "default_bind_address")]
    bind_address: String,
    #[serde(default = "default_port")]
    port: u16,
    #[serde(default = "default_health_endpoint")]
    health_endpoint: String,
    #[serde(default)]
    launch_args: Vec<String>,
}

impl Default for ComfySettings {
    fn default() -> Self {
        Self {
            repo_path: default_repo_path(),
            python_executable: String::new(),
            bind_address: default_bind_address(),
            port: default_port(),
            health_endpoint: default_health_endpoint(),
            launch_args: Vec::new(),
        }
    }
}

fn default_repo_path() -> String {
    "comfyui".to_string()
}

fn default_bind_address() -> String {
    "127.0.0.1".to_string()
}

fn default_health_endpoint() -> String {
    "/system_stats".to_string()
}

fn default_port() -> u16 {
    8188
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .expect("failed to resolve repo root from Tauri manifest dir")
}

fn settings_path(root: &Path) -> PathBuf {
    root.join("settings.json")
}

fn load_settings(root: &Path) -> Result<Settings, String> {
    let path = settings_path(root);
    if !path.exists() {
        return Ok(Settings::default());
    }

    let raw = fs::read_to_string(&path)
        .map_err(|err| format!("failed to read {}: {err}", path.display()))?;
    serde_json::from_str::<Settings>(&raw)
        .map_err(|err| format!("failed to parse {}: {err}", path.display()))
}

fn resolve_path(root: &Path, raw: &str) -> Option<PathBuf> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }

    let path = PathBuf::from(trimmed);
    if path.is_absolute() {
        Some(path)
    } else {
        Some(root.join(path))
    }
}

fn resolve_runtime_dir(root: &Path, settings: &Settings) -> PathBuf {
    resolve_path(root, &settings.tool_paths.runtime_dir).unwrap_or_else(|| root.join("tools").join("runtime"))
}

fn append_shell_log(message: &str) {
    let root = repo_root();
    let settings = load_settings(&root).unwrap_or_default();
    let runtime_dir = resolve_runtime_dir(&root, &settings);
    if fs::create_dir_all(&runtime_dir).is_err() {
        return;
    }
    let log_path = runtime_dir.join("tauri-shell.log");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = writeln!(file, "{}", message);
    }
}

fn resolve_python(root: &Path, settings: &Settings) -> PathBuf {
    if let Some(explicit) = resolve_path(root, &settings.comfyui.python_executable) {
        return explicit;
    }

    let venv_dir = resolve_path(root, &settings.tool_paths.venv_dir).unwrap_or_else(|| root.join(".venv"));
    if cfg!(windows) {
        venv_dir.join("Scripts").join("python.exe")
    } else {
        venv_dir.join("bin").join("python")
    }
}

fn resolve_shell_python(root: &Path, settings: &Settings) -> PathBuf {
    resolve_python(root, settings)
}

fn resolve_frontend_root(root: &Path, settings: &Settings) -> Option<PathBuf> {
    let python = resolve_python(root, settings);
    let venv_root = python.parent()?.parent()?;
    let candidates = [
        venv_root.join("Lib").join("site-packages").join("comfyui_frontend_package").join("static"),
        venv_root.join("Lib").join("site-packages").join("ComfyUI_frontend_package").join("static"),
        venv_root.join("lib").join("site-packages").join("comfyui_frontend_package").join("static"),
        venv_root.join("lib").join("site-packages").join("ComfyUI_frontend_package").join("static"),
    ];

    candidates.into_iter().find(|path| path.is_dir())
}

fn comfy_base_url(settings: &Settings) -> String {
    let bind = settings.comfyui.bind_address.trim();
    let browser_host = match bind {
        "0.0.0.0" | "::" | "[::]" => "127.0.0.1",
        _ => bind,
    };
    format!("http://{}:{}", browser_host, settings.comfyui.port)
}

fn health_url(settings: &Settings) -> String {
    format!("{}{}", comfy_base_url(settings), settings.comfyui.health_endpoint)
}

fn is_ready_blocking(url: &str) -> bool {
    let client = match BlockingClient::builder().timeout(Duration::from_secs(2)).build() {
        Ok(client) => client,
        Err(_) => return false,
    };

    match client.get(url).send() {
        Ok(response) => response.status().is_success(),
        Err(_) => false,
    }
}

fn open_log(path: &Path) -> Result<std::fs::File, String> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|err| format!("failed to open {}: {err}", path.display()))
}

fn spawn_backend(root: &Path, settings: &Settings) -> Result<Child, String> {
    let comfy_dir = resolve_path(root, &settings.comfyui.repo_path).unwrap_or_else(|| root.join("comfyui"));
    let comfy_main = comfy_dir.join("main.py");
    if !comfy_main.exists() {
        return Err(format!(
            "ComfyUI checkout not found at {}. Update settings.json or acquire the repo before launching the desktop shell.",
            comfy_dir.display()
        ));
    }

    let python = resolve_shell_python(root, settings);
    if !python.exists() {
        return Err(format!(
            "Python executable not found at {}. Configure settings.json or create the repo .venv first.",
            python.display()
        ));
    }

    let runtime_dir = resolve_runtime_dir(root, settings);
    fs::create_dir_all(&runtime_dir)
        .map_err(|err| format!("failed to create {}: {err}", runtime_dir.display()))?;

    let stdout_path = runtime_dir.join("tauri-comfyui-stdout.log");
    let stderr_path = runtime_dir.join("tauri-comfyui-stderr.log");
    let registry_path = runtime_dir.join("MasterPorts.json");
    let mut launch_args = settings.comfyui.launch_args.clone();
    if !launch_args.iter().any(|arg| arg == "--enable-manager") {
        launch_args.push("--enable-manager".to_string());
    }
    if !launch_args.iter().any(|arg| arg == "--listen") {
        launch_args.push("--listen".to_string());
        launch_args.push(settings.comfyui.bind_address.clone());
    }
    if !launch_args.iter().any(|arg| arg == "--port") {
        launch_args.push("--port".to_string());
        launch_args.push(settings.comfyui.port.to_string());
    }
    if !launch_args.iter().any(|arg| arg == "--front-end-root") {
        if let Some(frontend_root) = resolve_frontend_root(root, settings) {
            append_shell_log(&format!("using explicit frontend root: {}", frontend_root.display()));
            launch_args.push("--front-end-root".to_string());
            launch_args.push(frontend_root.to_string_lossy().into_owned());
        }
    }

    let mut command = Command::new(python);
    append_shell_log(&format!("spawning comfyui backend: {}", comfy_main.display()));
    command
        .arg(&comfy_main)
        .args(&launch_args)
        .current_dir(&comfy_dir)
        .env("MASTER_PORTS_PATH", registry_path)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::from(open_log(&stdout_path)?))
        .stderr(Stdio::from(open_log(&stderr_path)?));

    #[cfg(windows)]
    command.creation_flags(WINDOWS_BACKEND_CREATION_FLAGS);

    command.spawn().map_err(|err| {
        format!(
            "failed to launch ComfyUI from {}: {err}. See {} and {} for more detail.",
            comfy_main.display(),
            stdout_path.display(),
            stderr_path.display()
        )
    })
}

fn clear_stopped_child(state: &ShellState) -> Result<(), String> {
    let mut guard = state
        .child
        .lock()
        .map_err(|_| "failed to lock managed ComfyUI process state".to_string())?;

    let should_clear = match guard.as_mut() {
        Some(child) => child.try_wait().map_err(|err| err.to_string())?.is_some(),
        None => false,
    };

    if should_clear {
        *guard = None;
    }

    Ok(())
}

fn wait_until_ready_blocking(
    state: &ShellState,
    launch_url: String,
    health: String,
    reused_existing: bool,
) -> Result<LaunchResponse, String> {
    for _ in 0..180 {
        if is_ready_blocking(&health) {
            return Ok(LaunchResponse {
                url: launch_url,
                reused_existing,
            });
        }

        {
            let mut guard = state
                .child
                .lock()
                .map_err(|_| "failed to lock managed ComfyUI process state".to_string())?;
            if let Some(child) = guard.as_mut() {
                if let Some(status) = child.try_wait().map_err(|err| err.to_string())? {
                    *guard = None;
                    return Err(format!(
                        "ComfyUI exited before it became healthy ({status}). Check tools/runtime/tauri-comfyui-stderr.log."
                    ));
                }
            }
        }

        std::thread::sleep(Duration::from_secs(1));
    }

    Err("Timed out waiting for ComfyUI to respond on its health endpoint. Check tools/runtime/tauri-comfyui-stderr.log.".to_string())
}

fn launch_shell_blocking(state: &ShellState) -> Result<LaunchResponse, String> {
    let root = repo_root();
    let settings = load_settings(&root)?;
    let launch_url = comfy_base_url(&settings);
    let health = health_url(&settings);

    if is_ready_blocking(&health) {
        return Ok(LaunchResponse {
            url: launch_url,
            reused_existing: true,
        });
    }

    clear_stopped_child(state)?;

    let already_running = {
        let guard = state
            .child
            .lock()
            .map_err(|_| "failed to lock managed ComfyUI process state".to_string())?;
        guard.is_some()
    };

    if !already_running {
        let child = spawn_backend(&root, &settings)?;
        let mut guard = state
            .child
            .lock()
            .map_err(|_| "failed to lock managed ComfyUI process state".to_string())?;
        *guard = Some(child);
    }

    wait_until_ready_blocking(state, launch_url, health, false)
}

#[tauri::command]
async fn launch_comfyui_shell(app_handle: AppHandle) -> Result<LaunchResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state: State<'_, ShellState> = app_handle.state();
        launch_shell_blocking(&state)
    })
    .await
    .map_err(|err| format!("failed to join ComfyUI launch task: {err}"))?
}

fn js_string(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_string())
}

fn update_shell_status(window: &WebviewWindow, status: &str, details: &str) {
    let script = format!(
        "window.__COMFYAI_SET_STATUS && window.__COMFYAI_SET_STATUS({}, {});",
        js_string(status),
        js_string(details)
    );
    let _ = window.eval(&script);
}

fn update_shell_error(window: &WebviewWindow, message: &str) {
    let script = format!(
        "window.__COMFYAI_SET_ERROR && window.__COMFYAI_SET_ERROR({});",
        js_string(message)
    );
    let _ = window.eval(&script);
}

fn open_shell_url(window: &WebviewWindow, url: &str) -> Result<(), String> {
    update_shell_status(
        window,
        "ComfyUI is ready. Opening the editor.",
        &format!("Opening {}", url),
    );
    let parsed = Url::parse(url).map_err(|err| format!("failed to parse {}: {err}", url))?;
    window
        .navigate(parsed)
        .map_err(|err| format!("failed to navigate webview to {}: {err}", url))
}

fn bootstrap_shell(window: WebviewWindow, app_handle: AppHandle) {
    std::thread::spawn(move || {
        let state: State<'_, ShellState> = app_handle.state();
        append_shell_log("bootstrap_shell started");
        update_shell_status(
            &window,
            "Launching the local ComfyUI runtime.",
            "Starting ComfyUI and waiting for the configured health endpoint.",
        );
        match launch_shell_blocking(&state) {
            Ok(response) => {
                append_shell_log(&format!("backend ready: {}", response.url));
                if let Err(error) = open_shell_url(&window, &response.url) {
                    append_shell_log(&format!("webview navigation failed: {}", error));
                    update_shell_error(&window, &error);
                }
            }
            Err(error) => {
                append_shell_log(&format!("backend launch failed: {}", error));
                update_shell_error(&window, &error)
            }
        }
    });
}

fn stop_managed_backend(app: &AppHandle) {
    let state: State<'_, ShellState> = app.state();
    let lock_result = state.child.lock();
    if let Ok(mut guard) = lock_result {
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = None;
    }
}

fn main() {
    append_shell_log("main entered");
    let app = tauri::Builder::default()
        .manage(ShellState::default())
        .invoke_handler(tauri::generate_handler![launch_comfyui_shell])
        .setup(|app| {
            append_shell_log("setup entered");
            let window = app
                .get_webview_window("main")
                .ok_or_else(|| "main window not found".to_string())?;
            bootstrap_shell(window, app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building ComfyAI Tauri shell");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
            stop_managed_backend(app_handle);
        }
    });
}

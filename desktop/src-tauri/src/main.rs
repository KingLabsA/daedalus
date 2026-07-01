#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Command, Stdio, Child};
use std::sync::Mutex;
use tauri::Manager;

struct AgentState {
    process: Mutex<Option<Child>>,
}

#[tauri::command]
fn start_agent(state: tauri::State<AgentState>) -> Result<String, String> {
    let mut guard = state.process.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Err("Agent already running".into());
    }
    let child = Command::new("python3")
        .arg("agent_ultimate.py")
        .arg("ws")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to start agent: {}", e))?;
    *guard = Some(child);
    Ok("Agent started on ws://127.0.0.1:8765".into())
}

#[tauri::command]
fn stop_agent(state: tauri::State<AgentState>) -> Result<String, String> {
    let mut guard = state.process.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = guard.take() {
        child.kill().map_err(|e| format!("Failed to stop: {}", e))?;
        child.wait().ok();
        Ok("Agent stopped".into())
    } else {
        Err("No agent running".into())
    }
}

#[tauri::command]
fn agent_status(state: tauri::State<AgentState>) -> Result<String, String> {
    let mut guard = state.process.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_mut() {
        match child.try_wait() {
            Ok(None) => Ok("running".into()),
            _ => Ok("exited".into()),
        }
    } else {
        Ok("stopped".into())
    }
}

fn kill_agent(state: &AgentState) {
    if let Ok(mut guard) = state.process.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
            println!("🐍 Agent stopped");
        }
    }
}

fn main() {
    let builder = tauri::Builder::default()
        .manage(AgentState {
            process: Mutex::new(None),
        })
        .setup(|app| {
            // Auto-start agent
            let state: tauri::State<AgentState> = app.state();
            let mut guard = state.process.lock().expect("mutex poisoned");
            let child = Command::new("python3")
                .arg("agent_ultimate.py")
                .arg("ws")
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .spawn()
                .expect("Failed to spawn agent process");
            *guard = Some(child);
            println!("🐍 Agent auto-started on ws://127.0.0.1:8765");

            // System tray
            #[cfg(desktop)]
            {
                use tauri::tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent};
                let _tray = TrayIconBuilder::new()
                    .tooltip("Hermes-Ultimate Agent")
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                            let app = tray.app_handle();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                    })
                    .build(app)?;
            }

            // Global shortcut: CmdOrCtrl+Shift+H to toggle window
            #[cfg(desktop)]
            {
                use tauri::GlobalShortcutExt;
                let handle = app.handle().clone();
                app.global_shortcut().on_shortcut("CmdOrCtrl+Shift+H", move |_app, _shortcut, event| {
                    if let Some(w) = handle.get_webview_window("main") {
                        if w.is_visible().unwrap_or(false) {
                            let _ = w.hide();
                        } else {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                }).expect("Failed to register global shortcut");
            }

            // Window persistence: restore position/size from config
            #[cfg(desktop)]
            {
                if let Some(w) = app.get_webview_window("main") {
                    let conf_path = std::env::temp_dir().join("hermes_window_state.json");
                    if let Ok(data) = std::fs::read_to_string(&conf_path) {
                        if let Ok(state) = serde_json::from_str::<serde_json::Value>(&data) {
                            if let (Some(x), Some(y)) = (state["x"].as_i64(), state["y"].as_i64()) {
                                let _ = w.set_position(tauri::Position::Physical(tauri::PhysicalPosition { x, y }));
                            }
                            if let (Some(w_val), Some(h_val)) = (state["width"].as_i64(), state["height"].as_i64()) {
                                let _ = w.set_size(tauri::Size::Physical(tauri::PhysicalSize { width: w_val as u32, height: h_val as u32 }));
                            }
                        }
                    }
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![start_agent, stop_agent, agent_status]);

    builder
        .build(tauri::generate_context!())
        .expect("error building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::WindowEvent { label, event: win_event, .. } => {
                    if label == "main" {
                        if let tauri::WindowEvent::CloseRequested { .. } = &win_event {
                            // Save window state before closing
                            if let Some(w) = app_handle.get_webview_window("main") {
                                if let Ok(pos) = w.outer_position() {
                                    if let Ok(size) = w.outer_size() {
                                        let state = serde_json::json!({
                                            "x": pos.x, "y": pos.y,
                                            "width": size.width, "height": size.height,
                                        });
                                        let conf_path = std::env::temp_dir().join("hermes_window_state.json");
                                        let _ = std::fs::write(conf_path, state.to_string());
                                    }
                                }
                            }
                        }
                    }
                }
                tauri::RunEvent::Exit => {
                    let state: tauri::State<AgentState> = app_handle.state();
                    kill_agent(&state);
                }
                _ => {}
            }
        });
}

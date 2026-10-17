use std::sync::Arc;
use winit::{
    application::ApplicationHandler, event::*, event_loop::ActiveEventLoop, keyboard::PhysicalKey,
    window::Window,
};

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

use crate::state::{Extras, State};

// One-line status text under the canvas (web/*/index.html)
#[cfg(target_arch = "wasm32")]
fn set_status(text: &str) {
    if let Some(el) = wgpu::web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id("status"))
    {
        el.set_text_content(Some(text));
    }
}

/// `--render-frames DIR --camera a|b [--t0 0 --t1 10 --fps 60 --rate 1.0 --offset 0.0 --distractors]` (with `--size`):
/// frame i is rendered at replay time `offset + t0 + i / (fps * rate)`, i in 0..round((t1 - t0) * fps)
#[cfg(not(target_arch = "wasm32"))]
pub struct FrameJob {
    pub camera: String,
    pub t0: f64,
    pub t1: f64,
    pub fps: f64,
    pub rate: f64,
    pub offset: f64,
    pub distractors: bool,
}

/// `--screenshot out.png [--time s] [--preset 1..5] [--size WxH] [--eye x,y,z --target x,y,z] [--replay FILE]`,
/// or `--render-frames DIR ...` (`path` is then the output directory)
#[cfg(not(target_arch = "wasm32"))]
pub struct Shot {
    pub path: std::path::PathBuf,
    pub time: f64,
    pub preset: u8,
    pub size: (u32, u32),
    pub eye: Option<[f32; 3]>,
    pub target: Option<[f32; 3]>,
    pub frames: Option<FrameJob>,
}

#[cfg(not(target_arch = "wasm32"))]
impl Shot {
    /// None when `--screenshot` is absent (normal interactive run)
    pub fn from_args(args: impl IntoIterator<Item = String>) -> anyhow::Result<Option<Self>> {
        let mut shot = Shot {
            path: std::path::PathBuf::new(),
            time: 0.0,
            preset: 1,
            size: (1600, 900),
            eye: None,
            target: None,
            frames: None,
        };
        let mut job = FrameJob {
            camera: "a".into(),
            t0: 0.0,
            t1: 10.0,
            fps: 60.0,
            rate: 1.0,
            offset: 0.0,
            distractors: false,
        };
        let (mut found, mut frames_mode) = (false, false);
        let mut args = args.into_iter();
        let vec3 = |s: &str| -> anyhow::Result<[f32; 3]> {
            let v: Vec<f32> = s.split(',').map(str::parse).collect::<Result<_, _>>()?;
            v.try_into()
                .map_err(|_| anyhow::anyhow!("expected x,y,z, got {s}"))
        };
        while let Some(flag) = args.next() {
            let mut value = || {
                args.next()
                    .ok_or_else(|| anyhow::anyhow!("{flag} needs a value"))
            };
            match flag.as_str() {
                "--screenshot" => {
                    shot.path = value()?.into();
                    found = true;
                }
                "--render-frames" => {
                    shot.path = value()?.into();
                    frames_mode = true;
                    found = true;
                }
                "--camera" => job.camera = value()?,
                "--t0" => job.t0 = value()?.parse()?,
                "--t1" => job.t1 = value()?.parse()?,
                "--fps" => job.fps = value()?.parse()?,
                "--rate" => job.rate = value()?.parse()?,
                "--offset" => job.offset = value()?.parse()?,
                "--distractors" => job.distractors = true,
                "--time" => shot.time = value()?.parse()?,
                "--preset" => shot.preset = value()?.parse()?,
                "--size" => {
                    let v = value()?;
                    let (w, h) = v
                        .split_once('x')
                        .ok_or_else(|| anyhow::anyhow!("--size WxH"))?;
                    shot.size = (w.parse()?, h.parse()?);
                }
                "--replay" => {
                    let _ = crate::assets::REPLAY_OVERRIDE.set(value()?.into());
                }
                "--eye" => shot.eye = Some(vec3(&value()?)?),
                "--target" => shot.target = Some(vec3(&value()?)?),
                _ => {}
            }
        }
        shot.frames = frames_mode.then_some(job);
        Ok(found.then_some(shot))
    }
}

pub struct App {
    #[cfg(not(target_arch = "wasm32"))]
    shot: Option<Shot>,
    #[cfg(target_arch = "wasm32")]
    proxy: Option<winit::event_loop::EventLoopProxy<State>>,
    state: Option<State>,
    extras: Option<Box<dyn Extras>>,
}

impl App {
    pub fn new(
        #[cfg(target_arch = "wasm32")] event_loop: &winit::event_loop::EventLoop<State>,
        #[cfg(not(target_arch = "wasm32"))] shot: Option<Shot>,
        extras: Box<dyn Extras>,
    ) -> Self {
        #[cfg(target_arch = "wasm32")]
        let proxy = Some(event_loop.create_proxy());

        Self {
            #[cfg(not(target_arch = "wasm32"))]
            shot,
            state: None,
            extras: Some(extras),
            #[cfg(target_arch = "wasm32")]
            proxy,
        }
    }
}

impl ApplicationHandler<State> for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        // Define the window attributes
        #[allow(unused_mut)]
        let mut window_attributes = Window::default_attributes();

        #[cfg(target_arch = "wasm32")]
        {
            use wasm_bindgen::JsCast;
            use winit::platform::web::WindowAttributesExtWebSys;

            const CANVAS_ID: &str = "canvas";

            let window = wgpu::web_sys::window().unwrap_throw();
            let document = window.document().unwrap_throw();
            let canvas = document.get_element_by_id(CANVAS_ID).unwrap_throw();
            let html_canvas_element = canvas.unchecked_into();
            window_attributes = window_attributes.with_canvas(Some(html_canvas_element));
        }

        // Screenshot mode: hidden window at the requested size
        #[cfg(not(target_arch = "wasm32"))]
        if let Some(shot) = &self.shot {
            window_attributes = window_attributes
                .with_visible(false)
                .with_inner_size(winit::dpi::PhysicalSize::new(shot.size.0, shot.size.1));
        }

        // Create the window
        let window = Arc::new(event_loop.create_window(window_attributes).unwrap());

        let Some(extras) = self.extras.take() else {
            return;
        };
        // Generate a Future that creates our State struct
        #[cfg(not(target_arch = "wasm32"))]
        match pollster::block_on(State::new(window, extras)) {
            Ok(state) => self.state = Some(state),
            Err(e) => {
                log::error!("{e:#}");
                event_loop.exit();
            }
        }
        #[cfg(not(target_arch = "wasm32"))]
        if let (Some(shot), Some(state)) = (&self.shot, &mut self.state) {
            if let Err(e) = state.screenshot(shot) {
                log::error!("screenshot: {e:#}");
            }
            event_loop.exit();
        }

        #[cfg(target_arch = "wasm32")]
        {
            if let Some(proxy) = self.proxy.take() {
                wasm_bindgen_futures::spawn_local(async move {
                    match State::new(window, extras).await {
                        Ok(state) => {
                            set_status("running");
                            if proxy.send_event(state).is_err() {
                                log::error!("event loop closed before the scene was ready");
                            }
                        }
                        Err(e) => {
                            log::error!("{e:#}");
                            set_status(&format!("error: {e:#}"));
                        }
                    }
                });
            }
        }
    }

    fn user_event(&mut self, _event_loop: &ActiveEventLoop, #[allow(unused_mut)] mut event: State) {
        #[cfg(target_arch = "wasm32")]
        {
            event.window.request_redraw();
            event.resize(
                event.window.inner_size().width,
                event.window.inner_size().height,
            );
        }

        self.state = Some(event);
    }

    fn window_event(
        &mut self,
        event_loop: &ActiveEventLoop,
        _window_id: winit::window::WindowId,
        event: WindowEvent,
    ) {
        // State will be canvas
        let state = match &mut self.state {
            Some(canvas) => canvas,
            None => return,
        };

        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => state.resize(size.width, size.height),
            WindowEvent::RedrawRequested => {
                state.update();
                match state.render() {
                    Ok(_) => {}
                    Err(e) => {
                        log::error!("{e}");
                        event_loop.exit();
                    }
                }
            }
            WindowEvent::KeyboardInput {
                event:
                    KeyEvent {
                        physical_key: PhysicalKey::Code(code),
                        state: key_state,
                        repeat,
                        ..
                    },
                ..
            } => state.handle_key(event_loop, code, key_state.is_pressed(), repeat),
            WindowEvent::MouseInput {
                button,
                state: button_state,
                ..
            } => state.handle_mouse_button(button, button_state.is_pressed()),
            WindowEvent::Focused(false) => state.handle_focus_lost(),
            WindowEvent::MouseWheel { delta, .. } => state.handle_scroll(&delta),
            _ => {}
        }
    }

    // Raw mouse motion: keeps working at the window edge, unlike CursorMoved
    fn device_event(
        &mut self,
        _event_loop: &ActiveEventLoop,
        _device_id: DeviceId,
        event: DeviceEvent,
    ) {
        if let (Some(state), DeviceEvent::MouseMotion { delta: (dx, dy) }) =
            (&mut self.state, event)
        {
            state.handle_mouse_motion(dx, dy);
        }
    }
}

#[cfg(all(test, not(target_arch = "wasm32")))]
mod tests {
    use super::*;

    fn parse(args: &str) -> Option<Shot> {
        Shot::from_args(args.split_whitespace().map(String::from)).unwrap()
    }

    #[test]
    fn render_frames_flags_in_any_order() {
        let shot =
            parse("--camera b --rate 1.00004 --render-frames out/b --offset 0.0073 --t1 5 --size 1920x1080 --distractors")
                .unwrap();
        let job = shot.frames.unwrap();
        assert_eq!(shot.path, std::path::PathBuf::from("out/b"));
        assert_eq!(shot.size, (1920, 1080));
        assert_eq!(job.camera, "b");
        assert_eq!((job.t0, job.t1, job.fps), (0.0, 5.0, 60.0));
        assert_eq!((job.rate, job.offset), (1.00004, 0.0073));
        assert!(job.distractors);
    }

    #[test]
    fn screenshot_and_normal_runs_are_not_frame_jobs() {
        assert!(
            parse("--screenshot a.png --time 2")
                .unwrap()
                .frames
                .is_none()
        );
        assert!(parse("").is_none());
        assert!(parse("--camera b --rate 2").is_none());
    }
}

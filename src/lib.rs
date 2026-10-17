mod app;
mod assets;
mod calib;
// Public (hidden) only so oso-imu and benches can reach them
#[doc(hidden)]
pub mod imu;
mod playback;
mod renderer;
#[doc(hidden)]
pub mod replay;
#[doc(hidden)]
pub mod scene;
mod state;
#[doc(hidden)]
pub mod stats;
#[cfg(target_arch = "wasm32")]
mod web_api;

use app::App;
use winit::event_loop::EventLoop;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

#[cfg(target_arch = "wasm32")]
use winit::platform::web::EventLoopExtWebSys;

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen(start)]
pub fn run_web() -> anyhow::Result<(), wasm_bindgen::JsValue> {
    console_error_panic_hook::set_once();
    run().unwrap_throw();
    Ok(())
}

pub fn run() -> anyhow::Result<()> {
    // Initialize the logger
    #[cfg(not(target_arch = "wasm32"))]
    {
        env_logger::init();
    }

    #[cfg(target_arch = "wasm32")]
    {
        console_log::init_with_level(log::Level::Info).unwrap_throw();
    }

    // `--check-replay file`: parse it like the viewer does and print a summary (no window)
    #[cfg(not(target_arch = "wasm32"))]
    if let Some(path) = std::env::args()
        .skip_while(|a| a != "--check-replay")
        .nth(1)
    {
        let r = replay::Replay::parse(&std::fs::read(&path)?)?;
        println!(
            "{path}: ok, {} frames at {} fps, {} players, shuttle {}, racquet {}, {} events, {} strokes",
            r.frame_count,
            r.fps,
            r.players,
            r.has_shuttle(),
            r.has_racquet(),
            r.events().len(),
            r.strokes().len()
        );
        return Ok(());
    }

    // `--stats file`: per-player stroke stats of a replay as JSON (no window)
    #[cfg(not(target_arch = "wasm32"))]
    if let Some(path) = std::env::args().skip_while(|a| a != "--stats").nth(1) {
        println!(
            "{}",
            stats::to_json(&replay::Replay::parse(&std::fs::read(&path)?)?)
        );
        return Ok(());
    }

    // Create the event loop
    let event_loop = EventLoop::with_user_event().build()?;
    #[cfg(not(target_arch = "wasm32"))]
    {
        let mut app = App::new(app::Shot::from_args(std::env::args().skip(1))?);
        event_loop.run_app(&mut app)?;
    }
    #[cfg(target_arch = "wasm32")]
    {
        let app = App::new(&event_loop);
        event_loop.spawn_app(app);
    }

    Ok(())
}

//! Shared by Replay and Arena: the wgpu renderer, the scene, the replay.bin format and the viewer app.
//! An app supplies its own parts through [`Extras`] and calls [`run`].

pub mod app;
pub mod assets;
pub mod calib;
#[cfg(not(target_arch = "wasm32"))]
pub mod live;
pub mod playback;
pub mod renderer;
pub mod replay;
pub mod scene;
pub mod state;
#[cfg(target_arch = "wasm32")]
pub mod web_api;

pub use state::Extras;

use app::App;
use winit::event_loop::EventLoop;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

#[cfg(target_arch = "wasm32")]
use winit::platform::web::EventLoopExtWebSys;

/// Logger, `--check-replay`, then the viewer window (native) or the canvas (web) until it closes.
pub fn run(extras: Box<dyn Extras>) -> anyhow::Result<()> {
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

    // Create the event loop
    let event_loop = EventLoop::with_user_event().build()?;
    #[cfg(not(target_arch = "wasm32"))]
    {
        let mut app = App::new(app::Shot::from_args(std::env::args().skip(1))?, extras);
        event_loop.run_app(&mut app)?;
    }
    #[cfg(target_arch = "wasm32")]
    {
        let app = App::new(&event_loop, extras);
        event_loop.spawn_app(app);
    }

    Ok(())
}

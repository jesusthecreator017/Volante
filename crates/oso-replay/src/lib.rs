//! Replay app: recorded matches reconstructed in 3D. The shared renderer lives in `oso-render`;
//! this crate adds calibration-free extras: the IMU pipeline, match stats and the page's stroke data.

// Public (hidden) only so oso-imu and benches can reach them
#[doc(hidden)]
pub mod imu;
#[doc(hidden)]
pub mod stats;
#[cfg(target_arch = "wasm32")]
mod web_api;

use oso_render::Extras;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

struct ReplayExtras;

impl Extras for ReplayExtras {
    #[cfg(target_arch = "wasm32")]
    fn replay_loaded(&mut self, r: &oso_render::replay::Replay) {
        web_api::set_replay_data(
            r.strokes().concat(),
            (0..r.players).map(|p| r.head_speed(p)).collect(),
            stats::to_json(r),
        );
    }
}

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen(start)]
pub fn run_web() -> anyhow::Result<(), wasm_bindgen::JsValue> {
    console_error_panic_hook::set_once();
    run().unwrap_throw();
    Ok(())
}

pub fn run() -> anyhow::Result<()> {
    // `--stats file`: per-player stroke stats of a replay as JSON (no window)
    #[cfg(not(target_arch = "wasm32"))]
    if let Some(path) = std::env::args().skip_while(|a| a != "--stats").nth(1) {
        println!(
            "{}",
            stats::to_json(&oso_render::replay::Replay::parse(&std::fs::read(&path)?)?)
        );
        return Ok(());
    }
    oso_render::run(Box::new(ReplayExtras))
}

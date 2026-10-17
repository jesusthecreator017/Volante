//! Replay-only JS exports (wasm only): strokes, stats and head speed. The shared control surface (play, seek, status)
//! lives in `oso_render::web_api`.

use std::cell::RefCell;
use wasm_bindgen::prelude::*;

thread_local! {
    // Static replay data for the page: strokes N x 16 and per-player head speed, set once at load
    static STROKES: RefCell<Vec<f32>> = const { RefCell::new(Vec::new()) };
    static STATS: RefCell<String> = const { RefCell::new(String::new()) };
    static HEAD_SPEED: RefCell<Vec<Vec<f32>>> = const { RefCell::new(Vec::new()) };
}

pub fn set_replay_data(strokes: Vec<f32>, head_speed: Vec<Vec<f32>>, stats: String) {
    STATS.with(|c| *c.borrow_mut() = stats);
    STROKES.with(|s| *s.borrow_mut() = strokes);
    HEAD_SPEED.with(|h| *h.borrow_mut() = head_speed);
}

/// Strokes flattened row-major, N x 16 (columns: docs/data-format.md, `swings` track). Empty without the track.
#[wasm_bindgen]
pub fn oso_strokes() -> Vec<f32> {
    STROKES.with(|s| s.borrow().clone())
}

/// Names for the stroke_type column, in index order.
#[wasm_bindgen]
pub fn oso_stroke_types() -> Vec<String> {
    crate::imu::strokes::STROKE_TYPES
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// Per-frame racquet head speed (m/s) of `player`, NaN where the racquet has conf 0. Empty for an unknown player.
#[wasm_bindgen]
pub fn oso_head_speed(player: u32) -> Vec<f32> {
    HEAD_SPEED.with(|h| h.borrow().get(player as usize).cloned().unwrap_or_default())
}

/// Match stats as JSON (docs/data-format.md, "Match stats"); empty string before a replay loads.
#[wasm_bindgen]
pub fn oso_stats() -> String {
    STATS.with(|s| s.borrow().clone())
}

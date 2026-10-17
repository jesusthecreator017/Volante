//! Arena-only JS exports (wasm only). The shared control surface lives in `oso_render::web_api`.

use std::cell::RefCell;
use wasm_bindgen::prelude::*;

use oso_render::replay::{EVENT_FIELDS, EVENT_POINT, score_in};

thread_local! {
    static POINTS: RefCell<Vec<[f32; EVENT_FIELDS]>> = const { RefCell::new(Vec::new()) };
}

pub fn set_points(events: &[[f32; EVENT_FIELDS]]) {
    POINTS.with(|p| {
        *p.borrow_mut() = events
            .iter()
            .filter(|e| e[1] == EVENT_POINT)
            .copied()
            .collect()
    });
}

/// Match score `[player 0, player 1]` at time `t` from the `point` events; empty when the replay has none.
#[wasm_bindgen]
pub fn oso_score(t: f64) -> Vec<u32> {
    POINTS.with(|p| score_in(&p.borrow(), t).map_or(Vec::new(), |s| s.to_vec()))
}

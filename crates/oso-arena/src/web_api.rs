//! Arena-only JS exports (wasm only). The shared control surface lives in `oso_render::web_api`.

use std::cell::RefCell;
use wasm_bindgen::prelude::*;

use oso_render::replay::{EVENT_FIELDS, EVENT_POINT, MatchInfo, Replay, score_by_model};

thread_local! {
    static POINTS: RefCell<Vec<[f32; EVENT_FIELDS]>> = const { RefCell::new(Vec::new()) };
    static MATCH: RefCell<Option<MatchInfo>> = const { RefCell::new(None) };
}

/// Called once the replay parsed: keeps its point events and `match` object for `oso_score` and `oso_players`.
pub fn set_replay(r: &Replay) {
    POINTS.with(|p| {
        *p.borrow_mut() = r
            .events()
            .iter()
            .filter(|e| e[1] == EVENT_POINT)
            .copied()
            .collect()
    });
    MATCH.with(|m| *m.borrow_mut() = r.match_info().cloned());
}

/// Match score `[model A, model B]` at time `t` from the `point` events (by court side `[slot 0, slot 1]` for a replay without a `match`
/// header object); empty when the replay has none.
#[wasm_bindgen]
pub fn oso_score(t: f64) -> Vec<u32> {
    POINTS.with(|p| {
        MATCH.with(|m| {
            score_by_model(&p.borrow(), t, m.borrow().as_ref()).map_or(Vec::new(), |s| s.to_vec())
        })
    })
}

/// `{"a":{"name","build":{"power","speed","reach","accuracy"}},"b":{...}}` as a JSON string, or `""` when the replay has no `match` object.
#[wasm_bindgen]
pub fn oso_players() -> String {
    MATCH.with(|m| {
        m.borrow()
            .as_ref()
            .map_or(String::new(), MatchInfo::players_json)
    })
}

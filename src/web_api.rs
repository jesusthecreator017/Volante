//! JS control surface (wasm only). Buttons push the same `Command`s the keyboard does;
//! `State::update` drains the queue once per frame and publishes a status snapshot back.

use std::cell::{Cell, RefCell};
use wasm_bindgen::prelude::*;

use crate::playback::Command;
use crate::replay::{EVENT_FIELDS, EVENT_POINT};

thread_local! {
    // wasm is single-threaded, so a thread_local is a global
    static QUEUE: RefCell<Vec<Command>> = const { RefCell::new(Vec::new()) };
    static STATUS: Cell<Status> = const { Cell::new(Status::EMPTY) };
    // Static replay data for the page: strokes N x 16 and per-player head speed, set once at load
    static STROKES: RefCell<Vec<f32>> = const { RefCell::new(Vec::new()) };
    static STATS: RefCell<String> = const { RefCell::new(String::new()) };
    static POINTS: RefCell<Vec<[f32; EVENT_FIELDS]>> = const { RefCell::new(Vec::new()) };
    static HEAD_SPEED: RefCell<Vec<Vec<f32>>> = const { RefCell::new(Vec::new()) };
}

/// What the timeline UI polls each animation frame.
#[wasm_bindgen]
#[derive(Copy, Clone)]
pub struct Status {
    pub loaded: bool,
    pub time: f64,
    pub duration: f64,
    pub playing: bool,
    pub speed: f32,
    pub fps: f32,
    pub frame: u32,
    pub frame_count: u32,
    pub skeleton: bool,
    pub big_shuttle: bool,
    pub has_shuttle: bool,
    pub cameras: u32,
    pub stroke_count: u32,
}

impl Status {
    const EMPTY: Status = Status {
        loaded: false,
        time: 0.0,
        duration: 0.0,
        playing: false,
        speed: 1.0,
        fps: 60.0,
        frame: 0,
        frame_count: 0,
        skeleton: false,
        big_shuttle: false,
        has_shuttle: false,
        cameras: 0,
        stroke_count: 0,
    };
}

fn push(c: Command) {
    QUEUE.with(|q| q.borrow_mut().push(c));
}

/// Moves queued commands into `out`, oldest first.
pub fn drain_into(out: &mut Vec<Command>) {
    QUEUE.with(|q| out.append(&mut q.borrow_mut()));
}

pub fn set_replay_data(
    strokes: Vec<f32>,
    head_speed: Vec<Vec<f32>>,
    stats: String,
    events: &[[f32; EVENT_FIELDS]],
) {
    POINTS.with(|p| {
        *p.borrow_mut() = events
            .iter()
            .filter(|e| e[1] == EVENT_POINT)
            .copied()
            .collect()
    });
    STATS.with(|c| *c.borrow_mut() = stats);
    STROKES.with(|s| *s.borrow_mut() = strokes);
    HEAD_SPEED.with(|h| *h.borrow_mut() = head_speed);
}

pub fn set_status(s: Status) {
    STATUS.with(|c| c.set(s));
}

#[wasm_bindgen]
pub fn oso_toggle_play() {
    push(Command::TogglePlay);
}

#[wasm_bindgen]
pub fn oso_set_playing(playing: bool) {
    push(if playing {
        Command::Play
    } else {
        Command::Pause
    });
}

#[wasm_bindgen]
pub fn oso_seek(t: f64) {
    push(Command::Seek(t));
}

#[wasm_bindgen]
pub fn oso_step(n: i32) {
    push(Command::Step(n as i64));
}

#[wasm_bindgen]
pub fn oso_set_speed(s: f32) {
    push(Command::SetSpeed(s));
}

#[wasm_bindgen]
pub fn oso_preset(n: u8) {
    push(Command::Preset(n));
}

#[wasm_bindgen]
pub fn oso_toggle_skeleton() {
    push(Command::ToggleSkeleton);
}

#[wasm_bindgen]
pub fn oso_toggle_big_shuttle() {
    push(Command::ToggleBigShuttle);
}

/// Call `.free()` on the result when done (JS side holds a wasm object).
#[wasm_bindgen]
pub fn oso_status() -> Status {
    STATUS.with(|c| c.get())
}

#[wasm_bindgen]
pub fn oso_next_stroke() {
    push(Command::NextStroke);
}

#[wasm_bindgen]
pub fn oso_prev_stroke() {
    push(Command::PrevStroke);
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

/// Match score `[player 0, player 1]` at time `t` from the `point` events; empty when the replay has none.
#[wasm_bindgen]
pub fn oso_score(t: f64) -> Vec<u32> {
    POINTS.with(|p| crate::replay::score_in(&p.borrow(), t).map_or(Vec::new(), |s| s.to_vec()))
}

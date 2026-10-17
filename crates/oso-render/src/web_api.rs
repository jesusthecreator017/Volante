//! JS control surface shared by both apps (wasm only). Buttons push the same `Command`s the keyboard does;
//! `State::update` drains the queue once per frame and publishes a status snapshot back.

use std::cell::{Cell, RefCell};
use wasm_bindgen::prelude::*;

use crate::playback::Command;

thread_local! {
    // wasm is single-threaded, so a thread_local is a global
    static QUEUE: RefCell<Vec<Command>> = const { RefCell::new(Vec::new()) };
    static STATUS: Cell<Status> = const { Cell::new(Status::EMPTY) };
    static REPLAY_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
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

pub fn set_status(s: Status) {
    STATUS.with(|c| c.set(s));
}

/// Called by `State::new` (wasm only) with the replay load/parse error; read back by `oso_replay_error`.
pub fn set_replay_error(e: String) {
    REPLAY_ERROR.with(|c| *c.borrow_mut() = e);
}

/// Empty when the replay loaded or none was attempted, otherwise the load/parse error.
#[wasm_bindgen]
pub fn oso_replay_error() -> String {
    REPLAY_ERROR.with(|c| c.borrow().clone())
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

//! Playback controller: pure logic, no GPU and no windowing, so keyboard and web buttons share it.

use crate::replay::{frame_at, step_frame};

/// Speeds the keyboard cycles through.
pub const SPEED_STEPS: [f32; 6] = [0.1, 0.25, 0.5, 1.0, 1.5, 2.0];
const MIN_SPEED: f32 = 0.1;
const MAX_SPEED: f32 = 2.0;

/// Everything a UI can ask of the app; queued and applied in order once per frame.
// Play, Pause and Preset come from the web buttons only (NextStroke/PrevStroke from both; they need the replay, so State applies them); native keys 1-3 go through the camera controller.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
#[derive(Copy, Clone, Debug, PartialEq)]
pub enum Command {
    TogglePlay,
    Play,
    Pause,
    Seek(f64),
    Step(i64),
    SetSpeed(f32),
    Preset(u8),
    ToggleSkeleton,
    ToggleBigShuttle,
    NextStroke,
    PrevStroke,
}

pub struct Playback {
    /// Seconds into the replay, always within [0, duration)
    pub time: f64,
    pub duration: f64,
    pub playing: bool,
    pub speed: f32,
    pub looping: bool,
}

impl Playback {
    pub fn new(duration: f64) -> Self {
        Self {
            time: 0.0,
            duration,
            playing: true,
            speed: 1.0,
            looping: true,
        }
    }

    pub fn advance(&mut self, dt: f64) {
        if !self.playing || self.duration <= 0.0 {
            return;
        }
        let t = self.time + dt * self.speed as f64;
        if self.looping {
            self.time = t.rem_euclid(self.duration);
        } else if t >= self.duration {
            self.seek(self.duration);
            self.playing = false;
        } else {
            self.seek(t);
        }
    }

    pub fn toggle(&mut self) {
        self.playing = !self.playing;
    }

    pub fn seek(&mut self, t: f64) {
        // Half-open: duration itself would be frame 0 again
        self.time = t.clamp(0.0, (self.duration - 1e-6).max(0.0));
    }

    /// Moves `frames` frames, pauses, and lands mid-frame so floor(t * fps) cannot round to the neighbour.
    pub fn step(&mut self, frames: i64, fps: f32) {
        let count = (self.duration * fps as f64).round() as usize;
        let frame = step_frame(frame_at(self.time, fps, count), frames, count);
        self.playing = false;
        self.time = (frame as f64 + 0.5) / fps as f64;
    }

    pub fn set_speed(&mut self, s: f32) {
        self.speed = s.clamp(MIN_SPEED, MAX_SPEED);
    }

    /// The next entry of `SPEED_STEPS` above (`dir > 0`) or below the current speed; stays put at the ends.
    pub fn speed_step(&self, dir: i32) -> f32 {
        let s = self.speed;
        let steps = SPEED_STEPS.iter().copied();
        let next = if dir > 0 {
            steps.into_iter().find(|&x| x > s + 1e-4)
        } else {
            steps.rev().find(|&x| x < s - 1e-4)
        };
        next.unwrap_or(s)
    }

    /// Applies the playback commands; returns false for the ones this type does not own
    /// (`Preset`, `ToggleSkeleton`, `ToggleBigShuttle`, `NextStroke`, `PrevStroke`) so the caller can handle them.
    pub fn apply(&mut self, cmd: Command, fps: f32) -> bool {
        match cmd {
            Command::TogglePlay => self.toggle(),
            Command::Play => self.playing = true,
            Command::Pause => self.playing = false,
            Command::Seek(t) => self.seek(t),
            Command::Step(n) => self.step(n, fps),
            Command::SetSpeed(s) => self.set_speed(s),
            Command::Preset(_)
            | Command::ToggleSkeleton
            | Command::ToggleBigShuttle
            | Command::NextStroke
            | Command::PrevStroke => return false,
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FPS: f32 = 60.0;

    fn pb() -> Playback {
        Playback::new(10.0)
    }

    #[test]
    fn speed_and_seek_clamp() {
        let mut p = pb();
        p.set_speed(50.0);
        assert_eq!(p.speed, 2.0);
        p.set_speed(0.0);
        assert_eq!(p.speed, 0.1);
        p.seek(-3.0);
        assert_eq!(p.time, 0.0);
        p.seek(99.0);
        assert!(p.time < 10.0 && p.time > 9.99);
    }

    #[test]
    fn step_pauses_and_lands_on_frame_centers() {
        let mut p = pb();
        p.seek(1.0);
        p.step(1, FPS);
        assert!(!p.playing);
        assert_eq!(frame_at(p.time, FPS, 600), 61);
        assert!((p.time * 60.0 - 61.5).abs() < 1e-9);
        p.step(-2, FPS);
        assert_eq!(frame_at(p.time, FPS, 600), 59);
        p.seek(0.0);
        p.step(-1, FPS);
        assert_eq!(frame_at(p.time, FPS, 600), 599);
    }

    #[test]
    fn advance_scales_by_speed_and_wraps() {
        let mut p = pb();
        p.set_speed(0.5);
        p.advance(1.0);
        assert!((p.time - 0.5).abs() < 1e-9);
        p.seek(9.9);
        p.set_speed(2.0);
        p.advance(0.1);
        assert!((p.time - 0.1).abs() < 1e-9);
        p.playing = false;
        p.advance(1.0);
        assert!((p.time - 0.1).abs() < 1e-9);
        // Not looping: stops at the end
        let mut q = pb();
        q.looping = false;
        q.seek(9.95);
        q.advance(1.0);
        assert!(!q.playing && q.time > 9.99);
    }

    #[test]
    fn speed_steps_walk_the_list() {
        let mut p = pb();
        assert_eq!(p.speed_step(-1), 0.5);
        assert_eq!(p.speed_step(1), 1.5);
        p.set_speed(0.1);
        assert_eq!(p.speed_step(-1), 0.1);
        p.set_speed(2.0);
        assert_eq!(p.speed_step(1), 2.0);
        // Off-list speed (from a UI) snaps to the neighbours
        p.set_speed(0.3);
        assert_eq!(p.speed_step(1), 0.5);
        assert_eq!(p.speed_step(-1), 0.25);
    }

    #[test]
    fn commands_apply_in_order() {
        let mut p = pb();
        let cmds = [
            Command::Pause,
            Command::Seek(2.0),
            Command::Step(1),
            Command::SetSpeed(0.25),
            Command::TogglePlay,
        ];
        for c in cmds {
            assert!(p.apply(c, FPS));
        }
        assert!(p.playing && p.speed == 0.25);
        assert!((p.time - (121.5 / 60.0)).abs() < 1e-9);
        assert!(!p.apply(Command::Preset(2), FPS));
        assert!(!p.apply(Command::ToggleSkeleton, FPS));
        assert!(!p.apply(Command::ToggleBigShuttle, FPS));
        assert!(!p.apply(Command::NextStroke, FPS));
        assert!(!p.apply(Command::PrevStroke, FPS));
    }
}

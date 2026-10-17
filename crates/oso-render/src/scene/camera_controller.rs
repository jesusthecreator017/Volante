use winit::dpi::PhysicalPosition;
use winit::event::MouseScrollDelta;
use winit::keyboard::KeyCode;

use cgmath::InnerSpace;

use crate::scene::camera::Camera;
use crate::scene::hall;

// Fly/orbit controls: WASD/arrows + Q/E move, left-drag orbits, right-drag looks, scroll zooms, 1-3 presets
pub struct CameraController {
    rotate_horizontal: f32,
    rotate_vertical: f32,
    look_horizontal: f32,
    look_vertical: f32,
    // forward, back, left, right, up, down, fast
    keys: [bool; 7],
    scroll: f32,
    preset: Option<u8>,
    // Radians per pixel of mouse movement
    sensitivity: f32,
}

// ponytail: tuned on a trackpad. Mouse wheels send LineDelta, trackpads PixelDelta; adjust if either feels off.
// ponytail: the eye is kept inside the hall box (hall::clamp_eye); no collision with court/net/fixtures.
const MOVE_SPEED: f32 = 5.0;
const FAST_MULTIPLIER: f32 = 3.0;
const SCROLL_LINE: f32 = 1.0;
const SCROLL_PIXEL: f32 = 0.05;
// Each scroll line multiplies the distance by this
const ZOOM_PER_LINE: f32 = 0.9;

impl CameraController {
    pub fn new(sensitivity: f32) -> Self {
        Self {
            rotate_horizontal: 0.0,
            rotate_vertical: 0.0,
            look_horizontal: 0.0,
            look_vertical: 0.0,
            keys: [false; 7],
            scroll: 0.0,
            preset: None,
            sensitivity,
        }
    }

    pub fn handle_key(&mut self, code: KeyCode, is_pressed: bool) -> bool {
        let slot = match code {
            KeyCode::KeyW | KeyCode::ArrowUp => Some(0),
            KeyCode::KeyS | KeyCode::ArrowDown => Some(1),
            KeyCode::KeyA | KeyCode::ArrowLeft => Some(2),
            KeyCode::KeyD | KeyCode::ArrowRight => Some(3),
            KeyCode::KeyE => Some(4),
            KeyCode::KeyQ => Some(5),
            KeyCode::ShiftLeft | KeyCode::ShiftRight => Some(6),
            _ => None,
        };
        if let Some(i) = slot {
            self.keys[i] = is_pressed;
            // Not consumed: arrows/Shift/etc. may also mean something elsewhere
            return false;
        }
        let preset = match code {
            KeyCode::Digit1 => 1,
            KeyCode::Digit2 => 2,
            KeyCode::Digit3 => 3,
            _ => return false,
        };
        if is_pressed {
            self.preset = Some(preset);
        }
        true
    }

    // Window lost focus: key-up events will never arrive, so drop held keys
    pub fn clear_keys(&mut self) {
        self.keys = [false; 7];
    }

    // Several motion events can arrive per frame, so accumulate until update_camera.
    // `look`: right-drag rotates about the eye instead of orbiting the target.
    pub fn handle_mouse(&mut self, dx: f64, dy: f64, look: bool) {
        if look {
            self.look_horizontal += dx as f32;
            self.look_vertical += dy as f32;
        } else {
            self.rotate_horizontal += dx as f32;
            self.rotate_vertical += dy as f32;
        }
    }

    pub fn handle_scroll(&mut self, delta: &MouseScrollDelta) {
        self.scroll += match delta {
            MouseScrollDelta::LineDelta(_, y) => y * SCROLL_LINE,
            MouseScrollDelta::PixelDelta(PhysicalPosition { y, .. }) => *y as f32 * SCROLL_PIXEL,
        };
    }

    pub fn update_camera(&mut self, camera: &mut Camera, dt: f32) {
        // Dragging right swings the camera left around the target; dragging down raises it
        camera.yaw -= cgmath::Rad(self.rotate_horizontal * self.sensitivity);
        camera.pitch += cgmath::Rad(self.rotate_vertical * self.sensitivity);
        if self.look_horizontal != 0.0 || self.look_vertical != 0.0 {
            // Same angle deltas as the eye-offset orbit, but yaw flips sign (view = -offset); the eye stays put
            let eye = camera.eye();
            camera.yaw += cgmath::Rad(self.look_horizontal * self.sensitivity);
            camera.pitch += cgmath::Rad(self.look_vertical * self.sensitivity);
            camera.clamp();
            camera.target = eye + (camera.target - camera.eye());
        }
        camera.distance *= ZOOM_PER_LINE.powf(self.scroll);
        camera.clamp();
        // After the clamp so a calibrated preset keeps its exact pitch for this frame
        if let Some(preset) = self.preset.take() {
            camera.apply_preset(preset);
        }

        self.fly(camera, dt);
        // Every update, so orbiting/looking cannot push the eye through the floor, walls or ceiling either
        // (the target shifts by the correction, the eye follows)
        let eye = camera.eye();
        let eye = cgmath::Vector3::new(eye.x, eye.y, eye.z);
        camera.target += hall::clamp_eye(eye) - eye;

        self.scroll = 0.0;
        self.look_horizontal = 0.0;
        self.look_vertical = 0.0;
        self.rotate_horizontal = 0.0;
        self.rotate_vertical = 0.0;
    }
}

impl CameraController {
    // Moves the target (the eye follows). Forward is the full 3D view direction, strafe is horizontal.
    fn fly(&self, camera: &mut Camera, dt: f32) {
        let k = |i: usize| self.keys[i] as i32 as f32;
        let (fwd, side, up) = (k(0) - k(1), k(3) - k(2), k(4) - k(5));
        let view = (camera.target - camera.eye()).normalize();
        let right = view.cross(cgmath::Vector3::unit_y()).normalize();
        let wish = view * fwd + right * side + cgmath::Vector3::unit_y() * up;
        let speed = MOVE_SPEED * if self.keys[6] { FAST_MULTIPLIER } else { 1.0 };
        // Opposite keys cancel
        if wish.magnitude2() < 1e-6 {
            return;
        }
        // Normalized so diagonals are not faster
        camera.target += wish.normalize() * speed * dt;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drag_clamps_pitch_and_scroll_clamps_distance() {
        let mut camera = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        let mut controller = CameraController::new(0.01);

        controller.handle_mouse(0.0, 100_000.0, false);
        controller.update_camera(&mut camera, 0.016);
        assert!(camera.pitch.0 < std::f32::consts::FRAC_PI_2);

        controller.handle_scroll(&MouseScrollDelta::LineDelta(0.0, 1000.0));
        controller.update_camera(&mut camera, 0.016);
        assert_eq!(camera.distance, crate::scene::camera::MIN_DISTANCE);
    }

    #[test]
    fn digit_key_selects_preset() {
        let mut camera = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        let mut controller = CameraController::new(0.01);
        assert!(controller.handle_key(KeyCode::Digit3, true));
        controller.update_camera(&mut camera, 0.016);
        assert!(camera.eye().y > 10.0);
    }

    fn cam() -> Camera {
        Camera::look_from([0.0, 6.0, 0.0], [5.0, 5.5, 0.0])
    }

    fn held(codes: &[KeyCode]) -> CameraController {
        let mut c = CameraController::new(0.01);
        for k in codes {
            c.handle_key(*k, true);
        }
        c
    }

    #[test]
    fn w_moves_along_view_and_shift_triples() {
        let mut camera = cam();
        let eye0 = camera.eye();
        let view = (camera.target - eye0).normalize();
        held(&[KeyCode::KeyW]).update_camera(&mut camera, 1.0);
        assert!((camera.eye() - (eye0 + view * 5.0)).magnitude() < 0.01);
        let eye1 = camera.eye();
        held(&[KeyCode::ArrowUp, KeyCode::ShiftLeft]).update_camera(&mut camera, 1.0);
        assert!((camera.eye() - (eye1 + view * 15.0)).magnitude() < 0.01);
    }

    #[test]
    fn up_down_and_floor() {
        let mut camera = cam();
        held(&[KeyCode::KeyE]).update_camera(&mut camera, 1.0);
        assert!((camera.eye().y - 11.0).abs() < 0.01);
        held(&[KeyCode::KeyQ]).update_camera(&mut camera, 10.0);
        assert!((camera.eye().y - hall::MIN_EYE_HEIGHT).abs() < 1e-3);
    }

    #[test]
    fn ceiling_and_walls_stop_the_eye() {
        let mut camera = cam();
        held(&[KeyCode::KeyE]).update_camera(&mut camera, 10.0);
        assert!((camera.eye().y - (hall::CEILING_Y - hall::EYE_MARGIN)).abs() < 1e-3);
        let mut camera = Camera::look_from([0.0, 3.0, 0.0], [-1.0, 3.0, 0.0]);
        held(&[KeyCode::KeyW, KeyCode::ShiftLeft]).update_camera(&mut camera, 5.0);
        assert!((camera.eye().x - (hall::X_MIN + hall::EYE_MARGIN)).abs() < 1e-3);
    }

    #[test]
    fn diagonal_is_normalized() {
        let mut camera = cam();
        let eye0 = camera.eye();
        held(&[KeyCode::KeyW, KeyCode::KeyD]).update_camera(&mut camera, 1.0);
        assert!(((camera.eye() - eye0).magnitude() - 5.0).abs() < 0.01);
    }

    #[test]
    fn right_drag_keeps_eye_left_drag_keeps_target() {
        let mut camera = cam();
        let (eye, target) = (camera.eye(), camera.target);
        let mut c = CameraController::new(0.01);
        c.handle_mouse(40.0, 10.0, true);
        c.update_camera(&mut camera, 0.016);
        assert!((camera.eye() - eye).magnitude() < 1e-3);
        assert!((camera.target - target).magnitude() > 0.1);
        let target = camera.target;
        c.handle_mouse(40.0, 10.0, false);
        c.update_camera(&mut camera, 0.016);
        assert!((camera.target - target).magnitude() < 1e-6);
    }

    #[test]
    fn focus_loss_clears_movement() {
        let mut camera = cam();
        let mut c = held(&[KeyCode::KeyW, KeyCode::ShiftLeft]);
        c.clear_keys();
        let eye = camera.eye();
        c.update_camera(&mut camera, 1.0);
        assert!((camera.eye() - eye).magnitude() < 1e-6);
    }

    #[test]
    fn orbit_low_never_sinks_eye_and_can_look_up() {
        let mut camera = Camera::new((0.0, 1.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        let mut c = CameraController::new(0.01);
        c.handle_mouse(0.0, -100_000.0, false);
        c.update_camera(&mut camera, 0.016);
        assert!(camera.pitch.0 < 0.0);
        assert!(
            camera.eye().y >= hall::MIN_EYE_HEIGHT - 1e-4,
            "{:?}",
            camera.eye()
        );
    }
}

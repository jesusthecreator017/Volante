//! Smooth move between two camera poses (preset switches). Pure math: no GPU, no windowing.

use cgmath::Vector3;

/// Seconds a preset switch takes.
pub const DURATION: f32 = 0.4;

/// Where the camera is and what it sees: eye, look-at target and vertical field of view (radians).
#[derive(Copy, Clone, Debug, PartialEq)]
pub struct Pose {
    pub eye: [f32; 3],
    pub target: [f32; 3],
    pub fovy: f32,
}

fn lerp3(a: [f32; 3], b: [f32; 3], t: f32) -> [f32; 3] {
    (Vector3::from(a) + (Vector3::from(b) - Vector3::from(a)) * t).into()
}

/// Smoothstep: ease-in-out, 0 at 0, 1 at 1, monotonic.
fn ease(p: f32) -> f32 {
    p * p * (3.0 - 2.0 * p)
}

/// Pose at progress `p` (0..=1). The endpoints are returned exactly, not computed.
pub fn pose_at(from: &Pose, to: &Pose, p: f32) -> Pose {
    if p <= 0.0 {
        return *from;
    }
    if p >= 1.0 {
        return *to;
    }
    let e = ease(p);
    Pose {
        eye: lerp3(from.eye, to.eye, e),
        target: lerp3(from.target, to.target, e),
        fovy: from.fovy + (to.fovy - from.fovy) * e,
    }
}

/// A move in flight. Retargeting means dropping it and starting a new one from the current pose.
pub struct Transition {
    from: Pose,
    to: Pose,
    elapsed: f32,
}

impl Transition {
    pub fn new(from: Pose, to: Pose) -> Self {
        Self {
            from,
            to,
            elapsed: 0.0,
        }
    }

    /// Moves `dt` seconds on; returns the new pose and whether the move is finished.
    pub fn advance(&mut self, dt: f32) -> (Pose, bool) {
        self.elapsed += dt;
        let p = self.elapsed / DURATION;
        (pose_at(&self.from, &self.to, p), p >= 1.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::camera::Camera;
    use crate::scene::hall;
    use cgmath::InnerSpace;

    const A: Pose = Pose {
        eye: [-11.0, 4.0, 0.0],
        target: [0.0; 3],
        fovy: 0.78,
    };
    const B: Pose = Pose {
        eye: [0.0, 5.0, 12.0],
        target: [1.0, 0.0, 0.0],
        fovy: 1.0,
    };

    fn eye_dist(a: &Pose, b: &Pose) -> f32 {
        (Vector3::from(a.eye) - Vector3::from(b.eye)).magnitude()
    }

    #[test]
    fn endpoints_exact_and_progress_monotonic() {
        assert_eq!(pose_at(&A, &B, 0.0), A);
        assert_eq!(pose_at(&A, &B, 1.0), B);
        assert_eq!(pose_at(&A, &B, 7.0), B);
        let mut last = 0.0;
        for i in 1..=100 {
            let d = eye_dist(&A, &pose_at(&A, &B, i as f32 / 100.0));
            assert!(d >= last);
            last = d;
        }
        assert!((last - eye_dist(&A, &B)).abs() < 1e-4);
        // Ease-in-out: slow start, midpoint halfway
        assert!(eye_dist(&A, &pose_at(&A, &B, 0.1)) < 0.1 * eye_dist(&A, &B));
        assert!((eye_dist(&A, &pose_at(&A, &B, 0.5)) - 0.5 * eye_dist(&A, &B)).abs() < 1e-4);
    }

    #[test]
    fn advance_finishes_after_duration() {
        let mut t = Transition::new(A, B);
        let (_, done) = t.advance(DURATION * 0.5);
        assert!(!done);
        let (pose, done) = t.advance(DURATION * 0.5);
        assert!(done);
        assert_eq!(pose, B);
    }

    #[test]
    fn retarget_mid_flight_starts_from_current_pose() {
        let mut t = Transition::new(A, B);
        let (mid, _) = t.advance(DURATION * 0.3);
        let c = Pose {
            eye: [0.0, 11.0, 0.2],
            target: [0.0; 3],
            fovy: 0.78,
        };
        let mut t2 = Transition::new(mid, c);
        assert_eq!(t2.advance(0.0).0, mid);
        assert_eq!(t2.advance(DURATION).0, c);
    }

    #[test]
    fn presets_1_to_3_are_inside_the_hall() {
        for n in 1..=3 {
            let mut c = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
            c.apply_preset(n);
            let e = c.eye();
            let e = Vector3::new(e.x, e.y, e.z);
            assert!(
                (hall::clamp_eye(e) - e).magnitude() < 1e-3,
                "preset {n}: {e:?}"
            );
        }
    }
}

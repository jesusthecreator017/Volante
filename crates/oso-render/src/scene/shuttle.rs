//! Blocky shuttle and its trail. Mesh local frame: origin at the cork center, +Y = flight direction (cork leads,
//! the skirt trails behind along -Y).

use crate::renderer::lines::LineVertex;
use crate::replay::TrailPoint;
use crate::scene::court::{CpuMesh, finish, push_quad};
use crate::scene::racquet::push_box;
use cgmath::{InnerSpace, Quaternion, Vector3};

const CORK: f32 = 0.025;
const SKIRT_LEN: f32 = 0.06;
const SKIRT_SIDES: usize = 8;
const SKIRT_NARROW: f32 = 0.025;
const SKIRT_WIDE: f32 = 0.065;
/// Below this speed (m/s) the heading is not trusted and the previous orientation stays
pub const MIN_HEADING_SPEED: f32 = 0.5;
/// Seconds of trail
pub const TRAIL_WINDOW: f32 = 0.6;
const TRAIL_DETECTED: [f32; 3] = [1.0, 0.85, 0.1];
const TRAIL_FILLED: [f32; 3] = [1.0, 0.35, 0.0];
const TRAIL_OLDEST: f32 = 0.03;

/// One white mesh: the cork box plus an open 8-sided frustum skirt (two-sided so it shows from inside).
pub fn mesh() -> CpuMesh {
    let mut m = (Vec::new(), Vec::new());
    push_box(
        &mut m,
        Vector3::new(0.0, 0.0, 0.0),
        [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()],
        [CORK; 3],
    );
    let (y0, y1) = (-CORK / 2.0, -CORK / 2.0 - SKIRT_LEN);
    let ring = |k: usize, y: f32, d: f32| {
        let a = k as f32 * std::f32::consts::TAU / SKIRT_SIDES as f32;
        [d / 2.0 * a.cos(), y, d / 2.0 * a.sin()]
    };
    for k in 0..SKIRT_SIDES {
        let k1 = (k + 1) % SKIRT_SIDES;
        let q = [
            ring(k, y0, SKIRT_NARROW),
            ring(k1, y0, SKIRT_NARROW),
            ring(k1, y1, SKIRT_WIDE),
            ring(k, y1, SKIRT_WIDE),
        ];
        // Outward normal for this winding is away from the axis (checked in tests); the reversed copy faces inward
        push_quad(&mut m, q);
        push_quad(&mut m, [q[3], q[2], q[1], q[0]]);
    }
    finish(m)
}

/// Rotation taking +Y to the velocity direction (shortest arc); `prev` when the shuttle is nearly at rest.
pub fn orientation(prev: Quaternion<f32>, vel: Vector3<f32>) -> Quaternion<f32> {
    if vel.magnitude() < MIN_HEADING_SPEED {
        return prev;
    }
    Quaternion::from_arc(Vector3::unit_y(), vel.normalize(), None)
}

/// Appends the trail as LineList pairs (newest segment first): bright yellow now fading to near black at the oldest
/// point, orange where the segment's older end was physics-filled (conf 0.5).
pub fn trail_lines(points: &[TrailPoint], out: &mut Vec<LineVertex>) {
    for w in points.windows(2) {
        let (a, b) = (w[0], w[1]);
        let base = if a.conf.min(b.conf) < 1.0 {
            TRAIL_FILLED
        } else {
            TRAIL_DETECTED
        };
        let shade = |age: f32| {
            let k =
                TRAIL_OLDEST + (1.0 - TRAIL_OLDEST) * (1.0 - (age / TRAIL_WINDOW).clamp(0.0, 1.0));
            base.map(|c| c * k)
        };
        out.push(LineVertex {
            position: a.position,
            color: shade(a.age),
        });
        out.push(LineVertex {
            position: b.position,
            color: shade(b.age),
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::Rotation;

    #[test]
    fn mesh_bounds_and_winding() {
        let (v, idx) = mesh();
        let ys = v.iter().map(|v| v.position[1]);
        let (lo, hi) = ys.fold((f32::MAX, f32::MIN), |(l, h), y| (l.min(y), h.max(y)));
        assert!((hi - lo - 0.085).abs() < 1e-4, "length {}", hi - lo);
        assert!((hi - CORK / 2.0).abs() < 1e-6, "cork leads along +Y");
        let wide = v
            .iter()
            .map(|v| v.position[0])
            .fold(0.0f32, |m, x| m.max(x.abs()));
        assert!((wide - SKIRT_WIDE / 2.0).abs() < 1e-4);
        for tri in idx.chunks(3) {
            let p = |i: usize| Vector3::from(v[tri[i] as usize].position);
            let n = (p(1) - p(0)).cross(p(2) - p(0));
            assert!(n.magnitude() > 1e-12);
            assert!(n.normalize().dot(Vector3::from(v[tri[0] as usize].normal)) > 0.999);
        }
    }

    #[test]
    fn orientation_follows_velocity_and_holds_when_slow() {
        let prev = Quaternion::from_arc(Vector3::unit_y(), Vector3::unit_x(), None);
        for v in [
            Vector3::new(10.0, 3.0, -2.0),
            Vector3::new(0.0, -20.0, 0.0),
            Vector3::new(0.0, 5.0, 0.0),
        ] {
            let q = orientation(prev, v);
            let d = q.rotate_vector(Vector3::unit_y());
            assert!((d - v.normalize()).magnitude() < 1e-5, "{d:?} vs {v:?}");
        }
        assert_eq!(orientation(prev, Vector3::new(0.1, 0.1, 0.0)), prev);
    }

    #[test]
    fn trail_vertex_count_and_colors() {
        let pt = |x: f32, age: f32, conf: f32| TrailPoint {
            position: [x, 1.0, 0.0],
            conf,
            age,
        };
        // Newest first: now, 0.1 s, 0.3 s (physics-filled), 0.6 s
        let pts = [
            pt(3.0, 0.0, 1.0),
            pt(2.0, 0.1, 1.0),
            pt(1.0, 0.3, 0.5),
            pt(0.0, 0.6, 1.0),
        ];
        let mut out = Vec::new();
        trail_lines(&pts, &mut out);
        assert_eq!(out.len(), 6);
        assert_eq!(out[0].color, TRAIL_DETECTED);
        assert!(out[1].color[0] < out[0].color[0]);
        // Segments touching a filled point are orange; the oldest vertex is near black
        assert_eq!(
            out[2].color[1] / out[2].color[0],
            TRAIL_FILLED[1] / TRAIL_FILLED[0]
        );
        assert!(out[5].color.iter().all(|&c| c <= 0.04));
        assert!(trail_lines_empty());
    }

    // Per-frame cost: run with `cargo test --release bench_trail_lines -- --nocapture`
    #[test]
    fn bench_trail_lines() {
        let pts: Vec<TrailPoint> = (0..37)
            .map(|i| TrailPoint {
                position: [i as f32 * 0.1, 2.0, 0.0],
                conf: if i % 7 == 0 { 0.5 } else { 1.0 },
                age: i as f32 / 60.0,
            })
            .collect();
        let mut out = Vec::new();
        let start = web_time::Instant::now();
        for _ in 0..10_000 {
            out.clear();
            trail_lines(std::hint::black_box(&pts), &mut out);
        }
        println!(
            "trail_lines (37 points): {:?} per frame",
            start.elapsed() / 10_000
        );
    }

    fn trail_lines_empty() -> bool {
        let mut out = Vec::new();
        trail_lines(&[], &mut out);
        out.is_empty()
    }
}

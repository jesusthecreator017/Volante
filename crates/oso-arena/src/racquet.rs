//! Procedural badminton racquet for the Arena viewer (the Replay app keeps the OBJ, whose licence is unknown, so Arena never ships or
//! fetches it). Same frame as `oso_render::scene::racquet` and the `racquet` replay track: origin at the butt of the handle (where the
//! wrist holds it), +Y along the handle toward the tip, +Z normal to the string bed, +X across it. Real proportions: 0.665 m long, head
//! 0.230 m (Y) by 0.200 m (X), head centre 0.55 m from the butt (the point `animate.py` puts on the shuttle).
//!
//! Four flat-colored parts, because a mesh has one color: the frame (head ring and throat, tinted per model), the strings (a grid of thin
//! bars inside the ring), the shaft and the handle.

use cgmath::{InnerSpace, Vector3};
use oso_render::scene::court::{CpuMesh, finish};
use oso_render::scene::racquet::push_box;

pub const LENGTH: f32 = 0.665;
pub const HEAD_LENGTH: f32 = 0.230;
pub const HEAD_WIDTH: f32 = 0.200;
/// Head centre along the handle, from the butt
pub const HEAD_CENTER: f32 = LENGTH - HEAD_LENGTH / 2.0;
/// Handle (grip) from the butt
pub const HANDLE_LEN: f32 = 0.195;
const HANDLE_SIZE: [f32; 2] = [0.028, 0.022];
const SHAFT_THICKNESS: f32 = 0.009;
/// Where the throat leaves the shaft
const THROAT_Y: f32 = 0.34;
/// Where the two throat bars meet the head ring (x offset from the axis)
const THROAT_X: f32 = 0.045;
/// Frame tube: width in the string-bed plane, depth along the normal
const FRAME_THICKNESS: f32 = 0.012;
const FRAME_DEPTH: f32 = 0.016;
const HEAD_SEGMENTS: usize = 32;
const STRING_THICKNESS: f32 = 0.0016;
const STRING_DEPTH: f32 = 0.003;
/// Strings along the handle axis (mains) and across it (crosses)
const MAINS: usize = 9;
const CROSSES: usize = 11;

const SHAFT_COLOR: [f32; 4] = [0.10, 0.10, 0.10, 1.0];
const HANDLE_COLOR: [f32; 4] = [0.85, 0.85, 0.85, 1.0];
const STRING_COLOR: [f32; 4] = [0.9, 0.9, 0.9, 1.0];
/// The frame is the player's color, a little darker so it does not glow next to the body
const FRAME_TINT: f32 = 0.7;

/// `[frame, strings, shaft, handle]` with their flat colors; the frame is `tint` (a `PLAYER_COLORS` entry) scaled down.
pub fn parts(tint: [f32; 3]) -> Vec<(CpuMesh, [f32; 4])> {
    let (frame, strings, shaft, handle) = meshes();
    let [r, g, b] = tint.map(|c| c * FRAME_TINT);
    vec![
        (frame, [r, g, b, 1.0]),
        (strings, STRING_COLOR),
        (shaft, SHAFT_COLOR),
        (handle, HANDLE_COLOR),
    ]
}

fn axis_box(mesh: &mut CpuMesh, center: [f32; 3], size: [f32; 3]) {
    push_box(
        mesh,
        center.into(),
        [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()],
        size,
    );
}

/// A bar from `a` to `b` in the string-bed plane (z = 0), `thickness` wide in the plane and `depth` along the normal.
fn bar(mesh: &mut CpuMesh, a: (f32, f32), b: (f32, f32), thickness: f32, depth: f32) {
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    let along = Vector3::new(dx, dy, 0.0).normalize();
    let across = Vector3::new(-along.y, along.x, 0.0);
    push_box(
        mesh,
        Vector3::new((a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0, 0.0),
        [along, across, Vector3::unit_z()],
        [dx.hypot(dy), thickness, depth],
    );
}

/// Centre line of the head ring: semi-axes (x across, y along the handle); the ring's outer edge is this plus half the tube.
fn ring_axes() -> (f32, f32) {
    (
        (HEAD_WIDTH - FRAME_THICKNESS) / 2.0,
        (HEAD_LENGTH - FRAME_THICKNESS) / 2.0,
    )
}

/// The head ring alone (bars, no tangents yet)
fn head_ring() -> CpuMesh {
    let (ax, ay) = ring_axes();
    let mut frame = (Vec::new(), Vec::new());
    // Head ring: a polygon of bars whose corners lie on the centre-line ellipse; the wedge between neighbours is closed by
    // lengthening each bar so the corners meet on the outside
    let half_step = std::f32::consts::PI / HEAD_SEGMENTS as f32;
    let vertex = |k: usize| {
        let a = k as f32 * 2.0 * half_step;
        (ax * a.cos(), HEAD_CENTER + ay * a.sin())
    };
    for k in 0..HEAD_SEGMENTS {
        let (p, q) = (vertex(k), vertex((k + 1) % HEAD_SEGMENTS));
        let (dir, len) = ((q.0 - p.0, q.1 - p.1), (q.0 - p.0).hypot(q.1 - p.1));
        let u = (dir.0 / len, dir.1 / len);
        let ext = FRAME_THICKNESS * half_step.tan() / 2.0;
        bar(
            &mut frame,
            (p.0 - u.0 * ext, p.1 - u.1 * ext),
            (q.0 + u.0 * ext, q.1 + u.1 * ext),
            FRAME_THICKNESS,
            FRAME_DEPTH,
        );
    }
    frame
}

/// `(frame, strings, shaft, handle)`
pub fn meshes() -> (CpuMesh, CpuMesh, CpuMesh, CpuMesh) {
    let (ax, ay) = ring_axes();
    let mut frame = head_ring();
    // Throat: two bars from the shaft top out to the lower side of the ring
    let on_ring = |x: f32| (x, HEAD_CENTER - ay * (1.0 - (x / ax).powi(2)).sqrt());
    for side in [-1.0f32, 1.0] {
        let (x, y) = on_ring(side * THROAT_X);
        bar(
            &mut frame,
            (0.0, THROAT_Y),
            (x, y),
            SHAFT_THICKNESS,
            FRAME_DEPTH * 0.75,
        );
    }

    // Strings: mains parallel to the handle, crosses across it, each running to the ring's centre line
    let mut strings = (Vec::new(), Vec::new());
    for i in 0..MAINS {
        let x = ax * (2.0 * (i as f32 + 0.5) / MAINS as f32 - 1.0);
        let h = ay * (1.0 - (x / ax).powi(2)).sqrt();
        axis_box(
            &mut strings,
            [x, HEAD_CENTER, 0.0],
            [STRING_THICKNESS, 2.0 * h, STRING_DEPTH],
        );
    }
    for j in 0..CROSSES {
        let y = ay * (2.0 * (j as f32 + 0.5) / CROSSES as f32 - 1.0);
        let w = ax * (1.0 - (y / ay).powi(2)).sqrt();
        axis_box(
            &mut strings,
            [0.0, HEAD_CENTER + y, 0.0],
            [2.0 * w, STRING_THICKNESS, STRING_DEPTH],
        );
    }

    // Shaft from the handle top to the throat, then the handle
    let mut shaft = (Vec::new(), Vec::new());
    let shaft_len = THROAT_Y - HANDLE_LEN;
    axis_box(
        &mut shaft,
        [0.0, HANDLE_LEN + shaft_len / 2.0, 0.0],
        [SHAFT_THICKNESS, shaft_len, SHAFT_THICKNESS],
    );
    let mut handle = (Vec::new(), Vec::new());
    axis_box(
        &mut handle,
        [0.0, HANDLE_LEN / 2.0, 0.0],
        [HANDLE_SIZE[0], HANDLE_LEN, HANDLE_SIZE[1]],
    );
    (
        finish(frame),
        finish(strings),
        finish(shaft),
        finish(handle),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bounds(mesh: &CpuMesh) -> ([f32; 3], [f32; 3]) {
        let (mut lo, mut hi) = ([f32::MAX; 3], [f32::MIN; 3]);
        for v in &mesh.0 {
            for a in 0..3 {
                lo[a] = lo[a].min(v.position[a]);
                hi[a] = hi[a].max(v.position[a]);
            }
        }
        (lo, hi)
    }

    fn merged(list: &[&CpuMesh]) -> CpuMesh {
        let mut out = (Vec::new(), Vec::new());
        for m in list {
            let base = out.0.len() as u32;
            out.0.extend(m.0.iter().copied());
            out.1.extend(m.1.iter().map(|i| i + base));
        }
        out
    }

    #[test]
    fn whole_racquet_has_real_proportions_in_the_racquet_frame() {
        let (frame, strings, shaft, handle) = meshes();
        let (lo, hi) = bounds(&merged(&[&frame, &strings, &shaft, &handle]));
        // Butt at the origin, tip 0.665 m up +Y, centred on the handle axis, thin along the string-bed normal +Z
        assert!(lo[1].abs() < 1e-4, "{lo:?}");
        assert!((hi[1] - LENGTH).abs() < 0.004, "length {}", hi[1]);
        assert!((lo[0] + hi[0]).abs() < 1e-3 && (lo[2] + hi[2]).abs() < 1e-3);
        assert!(hi[2] - lo[2] < 0.03, "thickness {}", hi[2] - lo[2]);
    }

    #[test]
    fn head_is_230_by_200_mm_centred_55_cm_from_the_butt() {
        let (lo, hi) = bounds(&head_ring());
        assert!(
            (hi[0] - lo[0] - 0.200).abs() < 0.004,
            "width {}",
            hi[0] - lo[0]
        );
        assert!(
            (hi[1] - lo[1] - 0.230).abs() < 0.004,
            "head length {}",
            hi[1] - lo[1]
        );
        assert!(
            ((lo[1] + hi[1]) / 2.0 - 0.55).abs() < 0.002,
            "centre {}",
            (lo[1] + hi[1]) / 2.0
        );
        assert!((lo[0] + hi[0]).abs() < 1e-3, "off axis");
        assert!(
            (HEAD_CENTER - 0.55).abs() < 1e-6,
            "animate.py puts the shuttle 0.55 m from the grip"
        );
    }

    #[test]
    fn handle_is_at_the_butt_and_the_shaft_joins_it_to_the_head() {
        let (frame, strings, shaft, handle) = meshes();
        let (hlo, hhi) = bounds(&handle);
        assert!(hlo[1].abs() < 1e-4 && (hhi[1] - HANDLE_LEN).abs() < 1e-4);
        assert!((hhi[0] - hlo[0] - 0.028).abs() < 1e-4);
        let (slo, shi) = bounds(&shaft);
        // The shaft starts where the handle ends and reaches the throat; both sit on the axis
        assert!((slo[1] - HANDLE_LEN).abs() < 1e-4 && (shi[1] - THROAT_Y).abs() < 1e-4);
        assert!((slo[0] + shi[0]).abs() < 1e-5 && (slo[2] + shi[2]).abs() < 1e-5);
        // The head is entirely beyond the throat; the strings stay inside the ring's outer edge
        let (flo, _) = bounds(&frame);
        assert!(flo[1] >= HANDLE_LEN - 1e-4, "frame reaches into the handle");
        let (xlo, xhi) = bounds(&strings);
        assert!(
            xlo[1] > HEAD_CENTER - HEAD_LENGTH / 2.0 && xhi[1] < HEAD_CENTER + HEAD_LENGTH / 2.0
        );
        assert!(xhi[0] < HEAD_WIDTH / 2.0 && xlo[0] > -HEAD_WIDTH / 2.0);
        assert!(strings.0.len() / 24 == MAINS + CROSSES);
    }

    #[test]
    fn faces_point_outward_and_triangles_are_valid() {
        let (frame, strings, shaft, handle) = meshes();
        for (vertices, indices) in [&frame, &strings, &shaft, &handle] {
            assert_eq!(indices.len() % 6, 0);
            assert_eq!(vertices.len() / 4 * 6, indices.len());
            assert!(indices.iter().all(|&i| (i as usize) < vertices.len()));
            for tri in indices.chunks(3) {
                let p = |i: usize| Vector3::from(vertices[tri[i] as usize].position);
                let n = (p(1) - p(0)).cross(p(2) - p(0));
                assert!(n.magnitude() > 1e-12, "degenerate triangle");
                let stored = Vector3::from(vertices[tri[0] as usize].normal);
                assert!(n.normalize().dot(stored) > 0.999);
            }
            assert!(
                vertices
                    .iter()
                    .all(|v| v.position.iter().all(|c| c.is_finite()))
            );
        }
        // The handle's faces point away from its centre
        let mid = Vector3::new(0.0, HANDLE_LEN / 2.0, 0.0);
        for q in handle.0.chunks(4) {
            let c: Vector3<f32> = q
                .iter()
                .map(|v| Vector3::from(v.position))
                .sum::<Vector3<f32>>()
                / 4.0;
            assert!((c - mid).dot(Vector3::from(q[0].normal)) > 0.0);
        }
    }

    #[test]
    fn parts_tint_only_the_frame() {
        let a = parts([1.0, 0.45, 0.05]);
        let b = parts([0.0, 0.8, 1.0]);
        assert_eq!(a.len(), 4);
        assert_ne!(a[0].1, b[0].1);
        assert!(a[0].1[3] == 1.0 && a[0].1[0] > a[0].1[2]);
        for i in 1..4 {
            assert_eq!(a[i].1, b[i].1);
            assert_eq!(a[i].0.1, b[i].0.1);
        }
        // Opaque: alpha below 0.9 would make model.wgsl draw it as an unlit glow
        assert!(a.iter().all(|(_, c)| c[3] >= 0.9));
    }
}

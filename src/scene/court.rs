//! Badminton court geometry, CPU only. One function per part, each returns (vertices, indices).
//!
//! Court coordinate system (later phases rely on it):
//! - Units are meters. Origin is the court center on the floor.
//! - +Y is up. +X runs along the court length (baseline to baseline, x in [-6.70, 6.70]).
//!   +Z runs across the width (z in [-3.05, 3.05]).
//! - The net plane is x = 0. Posts stand at z = +-3.05.
//! - Doubles court 13.40 x 6.10, singles sidelines at z = +-2.59, short service lines at x = +-1.98,
//!   doubles long service lines at x = +-5.94, center line at z = 0 from the short service line to the baseline.
//! - Lines are 0.04 wide and lie inside the court dimensions: the outer edge of a line is the measurement.

use crate::renderer::model::{ModelVertex, compute_tangents};

pub type CpuMesh = (Vec<ModelVertex>, Vec<u32>);

pub const HALF_LENGTH: f32 = 6.70;
pub const HALF_WIDTH: f32 = 3.05;
const SINGLES_HALF_WIDTH: f32 = 2.59;
const SHORT_SERVICE_X: f32 = 1.98;
const LONG_SERVICE_X: f32 = 5.94;
const LINE_WIDTH: f32 = 0.04;
const LINE_Y: f32 = 0.002;
// Mat beyond the lines. Neighbouring courts are 2 m apart, so 0.8 each leaves 0.4 m of hall floor between mats.
const FLOOR_MARGIN: f32 = 0.8;

pub const NET_TOP_CENTER: f32 = 1.524;
pub const NET_TOP_POST: f32 = 1.55;
const NET_BOTTOM: f32 = 0.79;
/// White tape along the top of the net
pub const TAPE_HEIGHT: f32 = 0.075;
/// Mesh spacing of the net body
const NET_CELL: f32 = 0.061;
const POST_HALF: f32 = 0.03;

// Appends a quad whose vertices are counter-clockwise seen from the side the normal points to.
// Every quad gets a full 0..1 uv square so compute_tangents never sees degenerate uvs.
pub(crate) fn push_quad(mesh: &mut CpuMesh, p: [[f32; 3]; 4]) {
    use cgmath::{InnerSpace, Vector3};
    let v = |i: usize| Vector3::from(p[i]);
    let normal = (v(1) - v(0)).cross(v(2) - v(0)).normalize();
    let uvs = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]];
    let base = mesh.0.len() as u32;
    for (position, tex_coords) in p.into_iter().zip(uvs) {
        mesh.0.push(ModelVertex {
            position,
            tex_coords,
            normal: normal.into(),
            tangent: [0.0; 3],
            bitangent: [0.0; 3],
        });
    }
    mesh.1
        .extend_from_slice(&[base, base + 1, base + 2, base, base + 2, base + 3]);
}

// Horizontal rectangle facing +Y
pub(crate) fn push_flat(mesh: &mut CpuMesh, [x0, x1, z0, z1]: [f32; 4], y: f32) {
    push_quad(mesh, [[x0, y, z0], [x0, y, z1], [x1, y, z1], [x1, y, z0]]);
}

pub(crate) fn finish(mut mesh: CpuMesh) -> CpuMesh {
    compute_tangents(&mut mesh.0, &mesh.1);
    mesh
}

pub fn floor() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    push_flat(
        &mut mesh,
        [
            -HALF_LENGTH - FLOOR_MARGIN,
            HALF_LENGTH + FLOOR_MARGIN,
            -HALF_WIDTH - FLOOR_MARGIN,
            HALF_WIDTH + FLOOR_MARGIN,
        ],
        0.0,
    );
    finish(mesh)
}

// Line rectangles as [x0, x1, z0, z1]
fn line_rects() -> Vec<[f32; 4]> {
    let w = LINE_WIDTH;
    let mut r = Vec::new();
    for s in [-1.0f32, 1.0] {
        // Interval [a, b] mirrored to the other side of the origin when s < 0
        let side = |a: f32, b: f32| if s > 0.0 { (a, b) } else { (-b, -a) };
        // Baseline
        let (x0, x1) = side(HALF_LENGTH - w, HALF_LENGTH);
        r.push([x0, x1, -HALF_WIDTH, HALF_WIDTH]);
        // Doubles and singles sidelines, full length
        let (z0, z1) = side(HALF_WIDTH - w, HALF_WIDTH);
        r.push([-HALF_LENGTH, HALF_LENGTH, z0, z1]);
        let (z0, z1) = side(SINGLES_HALF_WIDTH - w, SINGLES_HALF_WIDTH);
        r.push([-HALF_LENGTH, HALF_LENGTH, z0, z1]);
        // Short service line, on the net side of 1.98
        let (x0, x1) = side(SHORT_SERVICE_X - w, SHORT_SERVICE_X);
        r.push([x0, x1, -HALF_WIDTH, HALF_WIDTH]);
        // Doubles long service line, on the net side of 5.94 (that edge is 0.76 from the baseline)
        let (x0, x1) = side(LONG_SERVICE_X - w, LONG_SERVICE_X);
        r.push([x0, x1, -HALF_WIDTH, HALF_WIDTH]);
        // Center line, short service line to baseline
        let (x0, x1) = side(SHORT_SERVICE_X, HALF_LENGTH - w);
        r.push([x0, x1, -w / 2.0, w / 2.0]);
    }
    r
}

pub fn lines() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    for rect in line_rects() {
        push_flat(&mut mesh, rect, LINE_Y);
    }
    finish(mesh)
}

// Top edge height of the net at lateral position z: straight lines from the post tops down to the center
pub fn net_top_y(z: f32) -> f32 {
    NET_TOP_CENTER + (NET_TOP_POST - NET_TOP_CENTER) * (z.abs() / HALF_WIDTH)
}

// The white top tape only: two segments (post to center to post), each emitted with both windings so culling never
// hides it. The see-through body is `net_lines`.
pub fn net() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    for (z0, z1) in [(-HALF_WIDTH, 0.0), (0.0, HALF_WIDTH)] {
        let p = [
            [0.0, net_top_y(z0) - TAPE_HEIGHT, z0],
            [0.0, net_top_y(z0), z0],
            [0.0, net_top_y(z1), z1],
            [0.0, net_top_y(z1) - TAPE_HEIGHT, z1],
        ];
        push_quad(&mut mesh, p);
        push_quad(&mut mesh, [p[0], p[3], p[2], p[1]]);
    }
    finish(mesh)
}

// Net body as line segments (pairs of endpoints) in the net plane x = 0: verticals and horizontals every ~6 cm from the
// bottom up to the tape. Draw them with the line pass; the mesh between them stays see-through.
pub fn net_lines() -> Vec<([f32; 3], [f32; 3])> {
    let mut lines = Vec::new();
    // Horizontals stop below the lowest tape edge (at the center)
    let top = NET_TOP_CENTER - TAPE_HEIGHT;
    let rows = ((top - NET_BOTTOM) / NET_CELL).round() as usize;
    for r in 0..=rows {
        let y = NET_BOTTOM + (top - NET_BOTTOM) * r as f32 / rows as f32;
        lines.push(([0.0, y, -HALF_WIDTH], [0.0, y, HALF_WIDTH]));
    }
    let cols = (2.0 * HALF_WIDTH / NET_CELL).round() as usize;
    for c in 0..=cols {
        let z = -HALF_WIDTH + 2.0 * HALF_WIDTH * c as f32 / cols as f32;
        lines.push(([0.0, NET_BOTTOM, z], [0.0, net_top_y(z) - TAPE_HEIGHT, z]));
    }
    lines
}

// Open-bottom box: 4 sides and a top
fn push_box(mesh: &mut CpuMesh, [x0, x1]: [f32; 2], [y0, y1]: [f32; 2], [z0, z1]: [f32; 2]) {
    push_flat(mesh, [x0, x1, z0, z1], y1);
    for (x, flip) in [(x1, false), (x0, true)] {
        let p = [[x, y0, z0], [x, y1, z0], [x, y1, z1], [x, y0, z1]];
        push_quad(mesh, if flip { [p[0], p[3], p[2], p[1]] } else { p });
    }
    for (z, flip) in [(z1, false), (z0, true)] {
        let p = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]];
        push_quad(mesh, if flip { [p[0], p[3], p[2], p[1]] } else { p });
    }
}

pub fn posts() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    for z in [-HALF_WIDTH, HALF_WIDTH] {
        push_box(
            &mut mesh,
            [-POST_HALF, POST_HALF],
            [0.0, NET_TOP_POST],
            [z - POST_HALF, z + POST_HALF],
        );
    }
    finish(mesh)
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f32 = 1e-4;
    type Bounds = (f32, f32, f32, f32);

    // Every 4 vertices are one quad: (min x, max x, min z, max z)
    fn quad_bounds(mesh: &CpuMesh) -> Vec<Bounds> {
        mesh.0
            .chunks(4)
            .map(|q| {
                let f = |k: usize, pick: fn(f32, f32) -> f32, init: f32| {
                    q.iter().fold(init, |a, v| pick(a, v.position[k]))
                };
                (
                    f(0, f32::min, f32::MAX),
                    f(0, f32::max, f32::MIN),
                    f(2, f32::min, f32::MAX),
                    f(2, f32::max, f32::MIN),
                )
            })
            .collect()
    }

    #[test]
    fn lines_stay_inside_court_and_touch_outer_edges() {
        let quads = quad_bounds(&lines());
        assert!(!quads.is_empty());
        for &(x0, x1, z0, z1) in &quads {
            assert!(
                x0 >= -HALF_LENGTH - EPS && x1 <= HALF_LENGTH + EPS,
                "{x0} {x1}"
            );
            assert!(
                z0 >= -HALF_WIDTH - EPS && z1 <= HALF_WIDTH + EPS,
                "{z0} {z1}"
            );
        }
        let has = |f: &dyn Fn(&Bounds) -> bool| quads.iter().any(f);
        assert!(has(&|q| (q.1 - HALF_LENGTH).abs() < EPS));
        assert!(has(&|q| (q.0 + HALF_LENGTH).abs() < EPS));
        assert!(has(&|q| (q.3 - HALF_WIDTH).abs() < EPS));
        assert!(has(&|q| (q.2 + HALF_WIDTH).abs() < EPS));
        // Singles sidelines
        assert!(has(&|q| (q.3 - 2.59).abs() < EPS));
        assert!(has(&|q| (q.2 + 2.59).abs() < EPS));
        // Service lines
        assert!(has(
            &|q| (q.1 - 1.98).abs() < EPS && (q.0 - 1.94).abs() < EPS
        ));
        assert!(has(&|q| (q.1 - 5.94).abs() < EPS));
    }

    #[test]
    fn net_top_heights() {
        let (vertices, _) = net();
        let top_at = |z: f32| {
            vertices
                .iter()
                .filter(|v| (v.position[2] - z).abs() < 1e-6)
                .map(|v| v.position[1])
                .fold(f32::MIN, f32::max)
        };
        assert!((top_at(0.0) - 1.524).abs() < 1e-4);
        assert!((top_at(3.05) - 1.55).abs() < 1e-4);
        assert!((top_at(-3.05) - 1.55).abs() < 1e-4);
    }

    #[test]
    fn net_body_lines_fill_the_gap_under_the_tape() {
        let lines = net_lines();
        // ~12 rows + ~101 columns
        assert!((100..=140).contains(&lines.len()), "{}", lines.len());
        for (a, b) in &lines {
            for p in [a, b] {
                assert_eq!(p[0], 0.0);
                assert!(p[1] >= NET_BOTTOM - 1e-5 && p[1] <= net_top_y(p[2]) - TAPE_HEIGHT + 1e-5);
                assert!(p[2].abs() <= HALF_WIDTH + 1e-5);
            }
        }
        // The tape mesh starts exactly where the grid ends: its lowest vertex at the center
        let low = net()
            .0
            .iter()
            .map(|v| v.position[1])
            .fold(f32::MAX, f32::min);
        assert!((low - (NET_TOP_CENTER - TAPE_HEIGHT)).abs() < 1e-4, "{low}");
    }

    #[test]
    fn floor_tangents_are_unit_and_normal_is_up() {
        for part in [floor(), lines(), net(), posts()] {
            for v in &part.0 {
                let len = |a: [f32; 3]| (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt();
                assert!((len(v.tangent) - 1.0).abs() < 1e-4, "{v:?}");
                assert!((len(v.bitangent) - 1.0).abs() < 1e-4, "{v:?}");
            }
        }
        assert!(floor().0.iter().all(|v| v.normal == [0.0, 1.0, 0.0]));
    }
}

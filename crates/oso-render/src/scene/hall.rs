//! Indoor sports hall around the court, CPU only. Same court coordinate system as `court.rs` (meters, our court centered
//! on the origin; all replay data lives in this frame). One function per part, each returns a `CpuMesh`.
//!
//! Layout (court centers x, z; every court is 13.40 x 6.10, 2 m gaps between neighbours):
//!
//! | row (x)   | z = 0 | z = +8.1 | z = +16.2 |
//! |-----------|-------|----------|-----------|
//! | x = 0     | OURS  | court    | court     |
//! | x = +15.4 | court | court    | court     |
//!
//! No court on the -Z side: the calibrated cameras at (+-7.2, 3.5, -5.55) stand in the 6.45 m strip between our sideline
//! (z = -3.05) and the wall at z = -9.5. Hall: x in [-12.0, 25.1] (preset 1 stands at x = -11, 5.3 m behind our baseline),
//! z in [-9.5, 22.25] (3 m behind the far sidelines), ceiling 11.5 m. Fixture rows run along X in the gaps between court
//! columns (z = -4.05, 4.05, 12.15, 20.25), at y = 11.0.

use crate::scene::court::{CpuMesh, finish, push_flat, push_quad};
use crate::scene::racquet::push_box;
use cgmath::Vector3;

/// Court centers `[x, z]`; index 0 is ours
pub const COURT_CENTERS: [[f32; 2]; 6] = [
    [0.0, 0.0],
    [0.0, 8.1],
    [0.0, 16.2],
    [15.4, 0.0],
    [15.4, 8.1],
    [15.4, 16.2],
];

pub const X_MIN: f32 = -12.0;
pub const X_MAX: f32 = 25.1;
pub const Z_MIN: f32 = -9.5;
pub const Z_MAX: f32 = 22.25;
pub const CEILING_Y: f32 = 11.5;
// Below the court mats (y = 0) so they sit on it. 1 cm, not 1 mm: depth precision at 30 m is about 1 mm.
const FLOOR_Y: f32 = -0.01;
pub const FIXTURE_Y: f32 = 11.0;
const FIXTURE_SIZE: [f32; 3] = [1.2, 0.1, 0.4];
const FIXTURE_ROWS_Z: [f32; 4] = [-4.05, 4.05, 12.15, 20.25];
const FIXTURE_X0: f32 = -9.0;
const FIXTURE_DX: f32 = 4.5;
const FIXTURES_PER_ROW: usize = 8;

/// Closest an eye may get to a wall or the ceiling
pub const EYE_MARGIN: f32 = 0.3;
/// Closest an eye may get to the floor
pub const MIN_EYE_HEIGHT: f32 = 0.1;

// Point light positions `[x, z]`: each sits under a fixture, over and around the main court. Only 8 lights fit the
// uniform; the other fixtures are emissive geometry only.
const LIGHT_XZ: [[f32; 2]; 8] = [
    [-4.5, -4.05],
    [4.5, -4.05],
    [-4.5, 4.05],
    [4.5, 4.05],
    [-4.5, 12.15],
    [4.5, 12.15],
    [13.5, 4.05],
    [18.0, 12.15],
];
const LIGHT_Y: f32 = 10.5;
pub const LIGHT_RANGE: f32 = 25.0;
/// Warm white, linear, times intensity
pub const LIGHT_COLOR: [f32; 3] = [30.0, 27.9, 24.6];

pub fn light_positions() -> impl Iterator<Item = [f32; 3]> {
    LIGHT_XZ.iter().map(|&[x, z]| [x, LIGHT_Y, z])
}

/// Keeps an eye inside the hall: 0.3 m off walls and ceiling, above the floor
pub fn clamp_eye(eye: Vector3<f32>) -> Vector3<f32> {
    Vector3::new(
        eye.x.clamp(X_MIN + EYE_MARGIN, X_MAX - EYE_MARGIN),
        eye.y.clamp(MIN_EYE_HEIGHT, CEILING_Y - EYE_MARGIN),
        eye.z.clamp(Z_MIN + EYE_MARGIN, Z_MAX - EYE_MARGIN),
    )
}

pub fn floor() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    push_flat(&mut mesh, [X_MIN, X_MAX, Z_MIN, Z_MAX], FLOOR_Y);
    finish(mesh)
}

pub fn ceiling() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    let y = CEILING_Y;
    // Wound the other way round than push_flat so it faces down
    push_quad(
        &mut mesh,
        [
            [X_MAX, y, Z_MIN],
            [X_MAX, y, Z_MAX],
            [X_MIN, y, Z_MAX],
            [X_MIN, y, Z_MIN],
        ],
    );
    finish(mesh)
}

/// Four walls facing inward
pub fn walls() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    // Corners in the order that makes each quad's normal point into the hall
    let corners = [
        [X_MIN, Z_MAX],
        [X_MIN, Z_MIN],
        [X_MAX, Z_MIN],
        [X_MAX, Z_MAX],
    ];
    for k in 0..4 {
        let ([ax, az], [bx, bz]) = (corners[k], corners[(k + 1) % 4]);
        push_quad(
            &mut mesh,
            [
                [ax, 0.0, az],
                [bx, 0.0, bz],
                [bx, CEILING_Y, bz],
                [ax, CEILING_Y, az],
            ],
        );
    }
    finish(mesh)
}

/// Light fixture boxes (drawn emissive), in rows between the court columns
pub fn fixtures() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    for z in FIXTURE_ROWS_Z {
        for k in 0..FIXTURES_PER_ROW {
            push_box(
                &mut mesh,
                Vector3::new(FIXTURE_X0 + FIXTURE_DX * k as f32, FIXTURE_Y, z),
                [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()],
                FIXTURE_SIZE,
            );
        }
    }
    finish(mesh)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::court::{HALF_LENGTH, HALF_WIDTH};
    use cgmath::InnerSpace;

    // [x0, x1, z0, z1]
    fn rect([x, z]: [f32; 2]) -> [f32; 4] {
        [
            x - HALF_LENGTH,
            x + HALF_LENGTH,
            z - HALF_WIDTH,
            z + HALF_WIDTH,
        ]
    }

    fn overlaps(a: [f32; 4], b: [f32; 4]) -> bool {
        a[0] < b[1] && b[0] < a[1] && a[2] < b[3] && b[2] < a[3]
    }

    fn inside_hall([x, y, z]: [f32; 3]) -> bool {
        (X_MIN..=X_MAX).contains(&x)
            && (0.0..=CEILING_Y).contains(&y)
            && (Z_MIN..=Z_MAX).contains(&z)
    }

    const CAMERAS: [[f32; 3]; 2] = [[7.2, 3.5, -5.55], [-7.2, 3.5, -5.55]];

    #[test]
    fn courts_do_not_overlap_and_leave_the_camera_zone_free() {
        for (i, a) in COURT_CENTERS.iter().enumerate() {
            for b in &COURT_CENTERS[i + 1..] {
                assert!(!overlaps(rect(*a), rect(*b)), "{a:?} {b:?}");
                // The 2 m gap holds on at least one axis
                let (dx, dz) = ((a[0] - b[0]).abs(), (a[1] - b[1]).abs());
                assert!(
                    dx >= 2.0 * HALF_LENGTH + 2.0 - 1e-4 || dz >= 2.0 * HALF_WIDTH + 2.0 - 1e-4
                );
            }
            // Camera zone: |x| <= 8 and z < -3.05
            assert!(
                !overlaps(rect(*a), [-8.0, 8.0, -100.0, -HALF_WIDTH]),
                "{a:?}"
            );
        }
        assert_eq!(COURT_CENTERS[0], [0.0, 0.0]);
    }

    #[test]
    fn walls_enclose_courts_with_margins() {
        for c in COURT_CENTERS {
            let r = rect(c);
            assert!(
                r[0] - X_MIN >= 3.0 && X_MAX - r[1] >= 3.0,
                "{c:?} behind baselines"
            );
            assert!(Z_MAX - r[3] >= 3.0, "{c:?} far sideline");
            assert!(r[2] - Z_MIN >= 4.0, "{c:?} camera side");
        }
        assert_eq!(CEILING_Y, 11.5);
        for cam in CAMERAS {
            assert!(inside_hall(cam), "{cam:?}");
            assert!(!COURT_CENTERS.iter().any(|&c| {
                let r = rect(c);
                (r[0]..=r[1]).contains(&cam[0]) && (r[2]..=r[3]).contains(&cam[2])
            }));
        }
    }

    #[test]
    fn camera_presets_1_to_3_are_inside_the_hall() {
        for preset in 1..=3 {
            let mut c = crate::scene::camera::Camera::new(
                (0.0, 0.0, 0.0),
                cgmath::Deg(0.0),
                cgmath::Deg(30.0),
                10.0,
            );
            c.apply_preset(preset);
            let e = c.eye();
            assert!(inside_hall([e.x, e.y, e.z]), "preset {preset}: {e:?}");
            // The clamp leaves them alone, so a preset is never nudged
            let v = Vector3::new(e.x, e.y, e.z);
            assert!(
                (clamp_eye(v) - v).magnitude() < 1e-4,
                "preset {preset}: {e:?}"
            );
        }
    }

    #[test]
    fn clamp_eye_stops_at_walls_ceiling_and_floor() {
        let e = clamp_eye(Vector3::new(-100.0, 100.0, 100.0));
        assert_eq!((e.x, e.y, e.z), (X_MIN + 0.3, CEILING_Y - 0.3, Z_MAX - 0.3));
        assert_eq!(clamp_eye(Vector3::new(0.0, -5.0, 0.0)).y, MIN_EYE_HEIGHT);
        let inside = Vector3::new(1.0, 2.0, 3.0);
        assert_eq!(clamp_eye(inside), inside);
    }

    #[test]
    fn walls_face_inward_and_ceiling_faces_down() {
        let center = Vector3::new(
            (X_MIN + X_MAX) / 2.0,
            CEILING_Y / 2.0,
            (Z_MIN + Z_MAX) / 2.0,
        );
        let (vertices, indices) = walls();
        assert_eq!(indices.len(), 4 * 6);
        for q in vertices.chunks(4) {
            let c = q
                .iter()
                .map(|v| Vector3::from(v.position))
                .sum::<Vector3<f32>>()
                / 4.0;
            assert!((center - c).dot(Vector3::from(q[0].normal)) > 0.0, "{q:?}");
        }
        assert!(
            ceiling()
                .0
                .iter()
                .all(|v| v.normal == [0.0, -1.0, 0.0] && v.position[1] == CEILING_Y)
        );
        assert!(
            floor()
                .0
                .iter()
                .all(|v| v.normal == [0.0, 1.0, 0.0] && v.position[1] < 0.0)
        );
    }

    #[test]
    fn fixtures_hang_in_the_gaps_and_every_light_has_one() {
        let (vertices, _) = fixtures();
        assert_eq!(vertices.len(), FIXTURE_ROWS_Z.len() * FIXTURES_PER_ROW * 24);
        for v in &vertices {
            let [x, y, z] = v.position;
            assert!(y < CEILING_Y && y > 10.0);
            assert!(inside_hall(v.position));
            // Never over a court's playing area (no glare), only in the gaps between and beside courts
            assert!(
                !COURT_CENTERS.iter().any(|&c| {
                    let r = rect(c);
                    x > r[0] && x < r[1] && z > r[2] && z < r[3]
                }),
                "{x} {z}"
            );
        }
        for [lx, ly, lz] in light_positions() {
            assert!(ly < FIXTURE_Y);
            assert!(
                vertices
                    .iter()
                    .any(|v| (v.position[0] - lx).abs() < 0.7 && (v.position[2] - lz).abs() < 0.25),
                "{lx} {lz}"
            );
        }
        assert_eq!(light_positions().count(), crate::scene::light::MAX_LIGHTS);
    }
}

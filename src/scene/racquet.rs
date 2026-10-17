//! Badminton racquet, CPU only: the OBJ model (`from_obj`) and a blocky fallback (`meshes`). Racquet local frame (same as the `racquet` replay track): origin at the butt
//! of the handle (where the IMU sits), +Y along the handle toward the tip, +Z normal to the string bed, +X across it.
//! The fallback has two meshes because a mesh has one flat color: the dark frame (shaft + head loop) and the white grip.

use crate::renderer::model::ModelVertex;
use crate::scene::court::{CpuMesh, finish, push_quad};
use cgmath::{InnerSpace, Vector3, Zero};

/// Racquet pose for a player without an IMU: butt at the right wrist (COCO 10), +Y along the forearm (elbow 8 -> wrist),
/// +Z horizontal and perpendicular to the forearm (bed faces sideways), +X = Y x Z. None unless both joints are tracked.
/// ponytail: right-handed assumption, forearm-aligned guess, not measured; an IMU on that player replaces it.
pub fn estimate_from_arm(
    joints: &[[f32; 4]; 17],
) -> Option<(Vector3<f32>, cgmath::Quaternion<f32>)> {
    let (w, e) = (joints[10], joints[8]);
    if w[3] <= 0.0 || e[3] <= 0.0 {
        return None;
    }
    let wrist = Vector3::new(w[0], w[1], w[2]);
    let forearm = wrist - Vector3::new(e[0], e[1], e[2]);
    if forearm.magnitude2() < 1e-8 {
        return None;
    }
    let y = forearm.normalize();
    let mut z = y.cross(Vector3::unit_y());
    if z.magnitude2() < 1e-6 {
        z = y.cross(Vector3::unit_x());
    }
    let z = z.normalize();
    let x = y.cross(z);
    Some((wrist, cgmath::Matrix3::from_cols(x, y, z).into()))
}

/// The IMU racquet when its conf > 0 at `t`, else the arm estimate from `joints` (the player's drawn, possibly held, pose).
pub fn pose_for(
    r: &crate::replay::Replay,
    t: f64,
    player: usize,
    joints: &[[f32; 4]; 17],
) -> Option<(Vector3<f32>, cgmath::Quaternion<f32>)> {
    match r.sample_racquet(t, player) {
        Some(p) => Some((p.position, p.rotation)),
        None => estimate_from_arm(joints),
    }
}

/// Total length, butt to top of the head (standard badminton racquet 0.675 m)
pub const LENGTH: f32 = 0.675;
const GRIP_SIZE: [f32; 3] = [0.028, 0.20, 0.028];
const SHAFT_THICKNESS: f32 = 0.008;
const SHAFT_LEN: f32 = 0.25;
const HEAD_WIDTH: f32 = 0.23;
const HEAD_LENGTH: f32 = 0.29;
/// Head center along the handle, from the butt. Mirrors src/imu/pose.rs.
pub const HEAD_CENTER: f32 = LENGTH - HEAD_LENGTH / 2.0;
/// Frame tube: thickness in the string-bed plane, depth along the normal
const FRAME_THICKNESS: f32 = 0.012;
const FRAME_DEPTH: f32 = 0.016;
const HEAD_SEGMENTS: usize = 12;

// Box centered at `center` with edge lengths `size` along the local axes `axes` (a right-handed frame): outward CCW faces
pub(crate) fn push_box(
    mesh: &mut CpuMesh,
    center: Vector3<f32>,
    axes: [Vector3<f32>; 3],
    size: [f32; 3],
) {
    for a in 0..3 {
        let (u, v) = ((a + 1) % 3, (a + 2) % 3);
        for s in [1.0f32, -1.0] {
            let corner = |du: f32, dv: f32| -> [f32; 3] {
                (center
                    + axes[a] * (s * size[a] / 2.0)
                    + axes[u] * (du * size[u] / 2.0)
                    + axes[v] * (dv * size[v] / 2.0))
                    .into()
            };
            // (u, v, a) is cyclic so u x v points along +a; flip the winding for the -a face
            let mut q = [
                corner(-1.0, -1.0),
                corner(1.0, -1.0),
                corner(1.0, 1.0),
                corner(-1.0, 1.0),
            ];
            if s < 0.0 {
                q.reverse();
            }
            push_quad(mesh, q);
        }
    }
}

fn axis_aligned(mesh: &mut CpuMesh, center: [f32; 3], size: [f32; 3]) {
    push_box(
        mesh,
        center.into(),
        [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()],
        size,
    );
}

/// `[frame, grip]`
pub fn meshes() -> [CpuMesh; 2] {
    let mut grip = (Vec::new(), Vec::new());
    axis_aligned(&mut grip, [0.0, GRIP_SIZE[1] / 2.0, 0.0], GRIP_SIZE);

    let mut frame = (Vec::new(), Vec::new());
    axis_aligned(
        &mut frame,
        [0.0, GRIP_SIZE[1] + SHAFT_LEN / 2.0, 0.0],
        [SHAFT_THICKNESS, SHAFT_LEN, SHAFT_THICKNESS],
    );
    // Oval loop of thin boxes. The polygon's flat sides (not its corners) touch the ellipse, so the outer edge
    // lands exactly on the head's width and length.
    let half_step = std::f32::consts::PI / HEAD_SEGMENTS as f32;
    let (ax, ay) = (
        (HEAD_WIDTH - FRAME_THICKNESS) / 2.0 / half_step.cos(),
        (HEAD_LENGTH - FRAME_THICKNESS) / 2.0 / half_step.cos(),
    );
    let vertex = |k: usize| {
        let a = half_step + k as f32 * 2.0 * half_step;
        (ax * a.cos(), HEAD_CENTER + ay * a.sin())
    };
    for k in 0..HEAD_SEGMENTS {
        let ((x0, y0), (x1, y1)) = (vertex(k), vertex((k + 1) % HEAD_SEGMENTS));
        let (dx, dy) = (x1 - x0, y1 - y0);
        let along = Vector3::new(dx, dy, 0.0).normalize();
        let across = Vector3::new(-along.y, along.x, 0.0);
        // Extended by the wedge between neighbouring boxes so corners close up on the outside
        let len = dx.hypot(dy) + FRAME_THICKNESS * half_step.tan();
        push_box(
            &mut frame,
            Vector3::new((x0 + x1) / 2.0, (y0 + y1) / 2.0, 0.0),
            [along, across, Vector3::unit_z()],
            [len, FRAME_THICKNESS, FRAME_DEPTH],
        );
    }
    [finish(frame), finish(grip)]
}

// The OBJ's .mtl was never provided, and its textures would not be flat colors anyway: tobj is handed this instead of the
// file, so every `usemtl` resolves and gets a flat gray (sRGB). Unknown material names fall back to FALLBACK_GRAY.
const OBJ_MTL: &str = "\
newmtl frame.002\nKd 0.9 0.9 0.9\n\
newmtl handle.002\nKd 0.75 0.75 0.75\n\
newmtl stringing.002\nKd 0.95 0.95 0.95\n\
newmtl stringswhite\nKd 0.95 0.95 0.95\n\
newmtl stringholderblack\nKd 0.05 0.05 0.05\n";
const FALLBACK_GRAY: f32 = 0.9;

/// Rigid map from OBJ space to the racquet local frame: `p' = R (p - origin) * scale`
struct Fit {
    // Rows are the new X, Y, Z axes expressed in OBJ space
    axes: [Vector3<f32>; 3],
    origin: Vector3<f32>,
    scale: f32,
}

impl Fit {
    // Long axis (largest extent) = handle axis, butt = the end the handle object sits at, thinnest axis = string-bed
    // normal (+Z). Origin at the butt on the handle axis, scaled so the length is `LENGTH`. No mirroring: X = Y x Z.
    fn derive(all: &[Vector3<f32>], handle: &[Vector3<f32>]) -> Self {
        let bounds = |pts: &[Vector3<f32>]| {
            pts.iter().fold(
                (
                    Vector3::new(f32::MAX, f32::MAX, f32::MAX),
                    Vector3::new(f32::MIN, f32::MIN, f32::MIN),
                ),
                |(lo, hi), p| {
                    (
                        Vector3::new(lo.x.min(p.x), lo.y.min(p.y), lo.z.min(p.z)),
                        Vector3::new(hi.x.max(p.x), hi.y.max(p.y), hi.z.max(p.z)),
                    )
                },
            )
        };
        let (lo, hi) = bounds(all);
        let (mid, extent) = ((lo + hi) / 2.0, hi - lo);
        let axis = |k: usize| [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()][k];
        let by_extent = |pick: fn(f32, f32) -> bool| {
            (0..3).fold(0, |best, k| {
                if pick(extent[k], extent[best]) {
                    k
                } else {
                    best
                }
            })
        };
        let (long, thin) = (by_extent(|a, b| a > b), by_extent(|a, b| a < b));

        let handle_mid = handle.iter().sum::<Vector3<f32>>() / handle.len().max(1) as f32;
        let butt_high = handle_mid[long] > mid[long];
        let mut origin = mid;
        origin[long] = if butt_high { hi[long] } else { lo[long] };

        let y = axis(long) * if butt_high { -1.0 } else { 1.0 };
        let z = axis(thin);
        Self {
            axes: [y.cross(z), y, z],
            origin,
            scale: LENGTH / extent[long],
        }
    }

    fn position(&self, p: Vector3<f32>) -> [f32; 3] {
        let q = (p - self.origin) * self.scale;
        self.axes.map(|a| a.dot(q))
    }

    fn direction(&self, d: Vector3<f32>) -> Vector3<f32> {
        Vector3::from(self.axes.map(|a| a.dot(d)))
    }
}

/// Parses the racquet OBJ (data only, nothing is executed or fetched) into one flat-colored mesh per material, already
/// in the racquet local frame. The file's `.mtl` is never read, see `OBJ_MTL`. Uvs are dropped (flat colors) and the
/// tangent frame is built from the normals, since the file's uvs can be degenerate.
pub fn from_obj(bytes: &[u8]) -> anyhow::Result<Vec<(CpuMesh, [f32; 4])>> {
    let (models, materials) = tobj::load_obj_buf(
        &mut std::io::Cursor::new(bytes),
        &tobj::LoadOptions {
            triangulate: true,
            single_index: true,
            ..Default::default()
        },
        |_| tobj::load_mtl_buf(&mut std::io::Cursor::new(OBJ_MTL)),
    )?;
    let materials = materials?;
    let points = |m: &tobj::Model| -> Vec<Vector3<f32>> {
        m.mesh
            .positions
            .as_chunks::<3>()
            .0
            .iter()
            .map(|p| Vector3::new(p[0], p[1], p[2]))
            .collect()
    };
    let all: Vec<_> = models.iter().flat_map(points).collect();
    let handle: Vec<_> = models
        .iter()
        .filter(|m| m.name.starts_with("handle"))
        .flat_map(points)
        .collect();
    anyhow::ensure!(
        !all.is_empty() && !handle.is_empty(),
        "racquet OBJ has no geometry or no handle object"
    );
    let fit = Fit::derive(&all, &handle);

    Ok(models
        .iter()
        .filter(|m| !m.mesh.indices.is_empty())
        .map(|m| {
            let mesh = &m.mesh;
            let vertices = (0..mesh.positions.len() / 3)
                .map(|i| {
                    let p = Vector3::new(
                        mesh.positions[i * 3],
                        mesh.positions[i * 3 + 1],
                        mesh.positions[i * 3 + 2],
                    );
                    let n = mesh
                        .normals
                        .get(i * 3..i * 3 + 3)
                        .map_or(Vector3::zero(), |n| {
                            fit.direction(Vector3::new(n[0], n[1], n[2]))
                        });
                    let n = if n.magnitude2() > 1e-12 {
                        n.normalize()
                    } else {
                        Vector3::unit_z()
                    };
                    let helper = if n.x.abs() < 0.9 {
                        Vector3::unit_x()
                    } else {
                        Vector3::unit_y()
                    };
                    let tangent = helper.cross(n).normalize();
                    ModelVertex {
                        position: fit.position(p),
                        tex_coords: [0.0, 0.0],
                        normal: n.into(),
                        tangent: tangent.into(),
                        bitangent: n.cross(tangent).into(),
                    }
                })
                .collect();
            let gray = mesh
                .material_id
                .and_then(|id| materials.get(id))
                .and_then(|m| m.diffuse)
                .map_or(FALLBACK_GRAY, |kd| kd[0]);
            ((vertices, mesh.indices.clone()), [gray, gray, gray, 1.0])
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::Rotation;

    #[test]
    fn arm_estimate_is_orthonormal_with_butt_at_wrist_and_y_along_forearm() {
        let mut j = [[0.0f32; 4]; 17];
        j[8] = [0.0, 1.2, 0.0, 1.0];
        j[10] = [0.3, 1.5, 0.1, 1.0];
        let (p, q) = estimate_from_arm(&j).unwrap();
        assert!((p - Vector3::new(0.3, 1.5, 0.1)).magnitude() < 1e-6);
        let fore = (Vector3::new(0.3, 0.3, 0.1)).normalize();
        let (x, y, z) = (
            q.rotate_vector(Vector3::unit_x()),
            q.rotate_vector(Vector3::unit_y()),
            q.rotate_vector(Vector3::unit_z()),
        );
        assert!((y - fore).magnitude() < 1e-5);
        assert!(x.dot(y).abs() < 1e-5 && x.dot(z).abs() < 1e-5 && y.dot(z).abs() < 1e-5);
        assert!((x.cross(y) - z).magnitude() < 1e-5, "right-handed");
        // Forearm straight up: the fallback axis still gives a valid frame
        j[10] = [0.0, 1.5, 0.0, 1.0];
        let (_, q) = estimate_from_arm(&j).unwrap();
        assert!((q.magnitude() - 1.0).abs() < 1e-5);
        j[8][3] = 0.0;
        assert!(estimate_from_arm(&j).is_none());
    }

    #[test]
    fn imu_pose_wins_when_conf_positive() {
        // Two players, 2 frames; player 0 has an IMU racquet (conf 1), player 1 conf 0
        let mut j = vec![[0.0f32; 4]; 2 * 2 * 17];
        for f in 0..2 {
            for p in 0..2 {
                j[(f * 2 + p) * 17 + 8] = [0.0, 1.0, 0.0, 1.0];
                j[(f * 2 + p) * 17 + 10] = [0.5, 1.0, 0.0, 1.0];
            }
        }
        let imu = [2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
        let none = [0.0; 9];
        let r = crate::replay::Replay::from_parts(
            10.0,
            2,
            2,
            j.clone(),
            Some(vec![imu, none, imu, none]),
        );
        let joints: [[f32; 4]; 17] = j[..17].try_into().unwrap();
        assert_eq!(
            pose_for(&r, 0.0, 0, &joints).unwrap().0,
            Vector3::new(2.0, 3.0, 4.0)
        );
        assert_eq!(
            pose_for(&r, 0.0, 1, &joints).unwrap().0,
            Vector3::new(0.5, 1.0, 0.0)
        );
        let r = crate::replay::Replay::from_parts(10.0, 2, 2, j, None);
        assert_eq!(
            pose_for(&r, 0.0, 0, &joints).unwrap().0,
            Vector3::new(0.5, 1.0, 0.0)
        );
    }

    fn bounds(meshes: &[CpuMesh]) -> ([f32; 3], [f32; 3]) {
        let (mut lo, mut hi) = ([f32::MAX; 3], [f32::MIN; 3]);
        for v in meshes.iter().flat_map(|m| &m.0) {
            for a in 0..3 {
                lo[a] = lo[a].min(v.position[a]);
                hi[a] = hi[a].max(v.position[a]);
            }
        }
        (lo, hi)
    }

    #[test]
    fn bounds_match_a_real_racquet() {
        let [frame, grip] = meshes();
        let (lo, hi) = bounds(&[frame, grip]);
        // Butt at the origin, tip at 0.675 m, head 0.23 m wide, thin along the string-bed normal
        assert!(lo[1].abs() < 1e-4, "{lo:?}");
        assert!(
            (hi[1] - lo[1] - 0.675).abs() < 0.01,
            "length {}",
            hi[1] - lo[1]
        );
        assert!(
            (hi[0] - lo[0] - 0.23).abs() < 0.01,
            "width {}",
            hi[0] - lo[0]
        );
        assert!(hi[2] - lo[2] < 0.04);
        assert!(
            (lo[0] + hi[0]).abs() < 1e-3 && (lo[2] + hi[2]).abs() < 1e-3,
            "centered on the handle axis"
        );
    }

    #[test]
    fn faces_point_outward_and_triangles_are_valid() {
        for (vertices, indices) in meshes() {
            assert_eq!(indices.len() % 6, 0);
            assert_eq!(vertices.len() / 4 * 6, indices.len());
            for tri in indices.chunks(3) {
                let p = |i: usize| Vector3::from(vertices[tri[i] as usize].position);
                let n = (p(1) - p(0)).cross(p(2) - p(0));
                assert!(n.magnitude() > 1e-9, "degenerate triangle");
                // Stored normal agrees with the winding
                let stored = Vector3::from(vertices[tri[0] as usize].normal);
                assert!(n.normalize().dot(stored) > 0.999);
            }
        }
        // A box's faces point away from its center: grip centered on x = z = 0
        let [_, (grip, _)] = meshes();
        for q in grip.chunks(4) {
            let c: Vector3<f32> = q
                .iter()
                .map(|v| Vector3::from(v.position))
                .sum::<Vector3<f32>>()
                / 4.0;
            let n = Vector3::from(q[0].normal);
            let mid = Vector3::new(0.0, 0.10, 0.0);
            assert!((c - mid).dot(n) > 0.0);
        }
    }

    const OBJ: &[u8] = include_bytes!("../../assets/models/racquet/WhiteBadmintonRacket.obj");

    #[test]
    fn obj_fits_the_racquet_frame() {
        let parts = from_obj(OBJ).unwrap();
        assert!(parts.len() >= 5, "{} parts", parts.len());
        let meshes: Vec<_> = parts.iter().map(|(m, _)| m.clone()).collect();
        let (lo, hi) = bounds(&meshes);
        // Butt at the origin, +Y to the tip, head 2 * 1.39 * 0.0704 wide, string-bed normal (thinnest) is Z
        assert!(lo[1].abs() < 1e-4, "{lo:?}");
        assert!((hi[1] - 0.675).abs() < 0.005, "length {}", hi[1]);
        assert!(
            (hi[0] - lo[0] - 0.195).abs() < 0.01,
            "width {}",
            hi[0] - lo[0]
        );
        assert!(hi[2] - lo[2] < hi[0] - lo[0] && hi[2] - lo[2] < hi[1] - lo[1]);
        assert!(hi[2] - lo[2] < 0.05, "thickness {}", hi[2] - lo[2]);
        assert!(
            (lo[0] + hi[0]).abs() < 0.005 && (lo[2] + hi[2]).abs() < 0.01,
            "centered on the handle axis"
        );

        // The grip (gray 0.75) hugs the butt; the frame (0.9) reaches into the head
        let ys = |gray: f32| -> Vec<f32> {
            parts
                .iter()
                .filter(|(_, c)| c[0] == gray)
                .flat_map(|((v, _), _)| v.iter().map(|v| v.position[1]))
                .collect()
        };
        assert!(
            ys(0.75).iter().all(|&y| y < 0.25),
            "grip reaches into the head"
        );
        assert!(ys(0.9).iter().any(|&y| y > 0.6));
        assert!(
            meshes
                .iter()
                .flat_map(|m| &m.0)
                .any(|v| v.position[1] > 0.45)
        );
    }

    #[test]
    fn obj_vertices_are_finite_unit_frames_and_colored() {
        for ((vertices, indices), color) in from_obj(OBJ).unwrap() {
            assert!(!indices.is_empty() && indices.iter().all(|&i| (i as usize) < vertices.len()));
            for v in &vertices {
                for a in [v.position, v.normal, v.tangent, v.bitangent] {
                    assert!(a.iter().all(|c| c.is_finite()), "{v:?}");
                }
                let len = |a: [f32; 3]| Vector3::from(a).magnitude();
                assert!((len(v.normal) - 1.0).abs() < 1e-3 && (len(v.tangent) - 1.0).abs() < 1e-3);
            }
            assert!(color[0] > 0.0 && color[0] < 1.0 && color[3] == 1.0);
        }
    }

    #[test]
    fn obj_without_mtl_still_gets_per_material_grays() {
        // The test asset has no .mtl next to it either way; from_obj never reads one
        let grays: Vec<f32> = from_obj(OBJ).unwrap().iter().map(|(_, c)| c[0]).collect();
        assert!(
            grays.contains(&0.05)
                && grays.contains(&0.75)
                && grays.contains(&0.95)
                && grays.contains(&0.9),
            "{grays:?}"
        );
    }

    #[test]
    fn fit_keeps_handedness_for_a_flipped_model() {
        // Butt at -X end, thin along Y: the same racquet rotated; result is still butt-at-origin, +Y to the tip
        let pts = [
            Vector3::new(-5.0, 0.0, 0.0),
            Vector3::new(5.0, 0.0, 0.0),
            Vector3::new(4.0, 0.3, 1.0),
            Vector3::new(4.0, 0.3, -1.0),
        ];
        let fit = Fit::derive(&pts, &pts[..1]);
        assert!(fit.position(pts[0])[1].abs() < 1e-5);
        assert!((fit.position(pts[1])[1] - LENGTH).abs() < 1e-5);
        assert!(
            (fit.axes[0].cross(fit.axes[1]) - fit.axes[2]).magnitude() < 1e-6,
            "mirrored"
        );
    }
}

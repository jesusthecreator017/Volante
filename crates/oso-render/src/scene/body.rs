//! Body orientation from the COCO-17 joints each frame: one stable rotation per body part, plus per-player `Dims`.
//! `scene::rig` poses the skinned humanoid from these.
//!
//! Every part frame has local +Y along the bone, local +X = body forward and local +Z = body right
//! (X x Y = Z, so the basis is right-handed: forward x up = right).

use cgmath::{InnerSpace, Matrix3, Quaternion, Vector3};

use crate::replay::{self, JOINTS, Replay};
use crate::scene::components::Transform;
use crate::scene::court::{CpuMesh, finish};

type V = Vector3<f32>;
pub type Joints = [[f32; 4]; JOINTS];

pub const PARTS: usize = 12;

pub const HEAD: usize = 0;
pub const TORSO: usize = 1;
// 2..10 limbs in LIMBS order, then the feet
pub const FOOT_L: usize = 10;
pub const FOOT_R: usize = 11;

const SHADOW_RADIUS: f32 = 0.35;
const SHADOW_Y: f32 = 0.003;
const SHADOW_SEGMENTS: usize = 16;

/// Tags a player's blob shadow entity.
#[derive(Copy, Clone, Debug)]
pub struct BodyPart {
    pub player: usize,
}

struct Limb {
    joints: (usize, usize),
    // Legs twist toward the horizontal facing, arms toward the torso's forward
    leg: bool,
}

// L before R; the COCO odd indices are left
const LIMBS: [Limb; 8] = [
    Limb {
        joints: (5, 7),
        leg: false,
    },
    Limb {
        joints: (6, 8),
        leg: false,
    },
    Limb {
        joints: (7, 9),
        leg: false,
    },
    Limb {
        joints: (8, 10),
        leg: false,
    },
    Limb {
        joints: (11, 13),
        leg: true,
    },
    Limb {
        joints: (12, 14),
        leg: true,
    },
    Limb {
        joints: (13, 15),
        leg: true,
    },
    Limb {
        joints: (14, 16),
        leg: true,
    },
];

/// Per-player body measurements in meters, fixed after load.
#[derive(Copy, Clone, Debug, PartialEq)]
pub struct Dims {
    /// upper arm, forearm, thigh, shin
    pub len: [f32; 4],
    pub torso_w: f32,
    pub torso_h: f32,
}

impl Default for Dims {
    fn default() -> Self {
        Self {
            len: [0.30, 0.26, 0.42, 0.42],
            torso_w: 0.36,
            torso_h: 0.50,
        }
    }
}

/// How long a player with no usable body (`hip_center` None) keeps the last pose that had one
pub const HOLD_S: f64 = 1.0;

/// `Replay::sample`, but when the sampled body is unusable the player's last frame that had one (within HOLD_S before `t`) is
/// returned whole, never mixing joints of different frames. Stateless: scans back from the frame, so seeking just works.
pub fn sample_held(r: &Replay, t: f64, player: usize) -> Joints {
    let j = r.sample(t, player);
    if crate::scene::rig::hip_center(&j).is_some() {
        return j;
    }
    let f0 = replay::frame_at(t, r.fps, r.frame_count);
    let back = (HOLD_S * r.fps as f64).floor() as usize;
    (f0.saturating_sub(back)..=f0)
        .rev()
        .map(|f| r.joints(f, player))
        .find(|j| crate::scene::rig::hip_center(j).is_some())
        .copied()
        .unwrap_or(j)
}

fn pos(j: &[f32; 4]) -> V {
    V::new(j[0], j[1], j[2])
}

fn ok(j: &Joints, i: usize) -> bool {
    j[i][3] > 0.0
}

// The six measured lengths of one frame (upper arm, forearm, thigh, shin, shoulder width, torso height),
// each only when every joint it needs has conf 1.0.
fn frame_lengths(j: &Joints) -> [Option<f32>; 6] {
    let full = |ids: &[usize]| ids.iter().all(|&i| j[i][3] >= 1.0);
    let dist = |a: usize, b: usize| (pos(&j[a]) - pos(&j[b])).magnitude();
    let limb = |a: usize, b: usize| full(&[a, b]).then(|| dist(a, b));
    let mid = |a: usize, b: usize| (pos(&j[a]) + pos(&j[b])) / 2.0;
    [
        // Left and right share a slot, so Dims is per player not per side
        limb(5, 7).or(limb(6, 8)),
        limb(7, 9).or(limb(8, 10)),
        limb(11, 13).or(limb(12, 14)),
        limb(13, 15).or(limb(14, 16)),
        limb(5, 6),
        full(&[5, 6, 11, 12]).then(|| (mid(5, 6) - mid(11, 12)).magnitude()),
    ]
}

impl Dims {
    /// Dimensions of the first frame's pose, for tests and one-off use.
    pub fn from_frame(j: &Joints) -> Dims {
        Self::from_lengths(frame_lengths(j).map(|l| vec![l.unwrap_or(f32::NAN)]))
    }

    fn from_lengths(per_slot: [Vec<f32>; 6]) -> Dims {
        let d = Dims::default();
        let med = |mut v: Vec<f32>, default: f32| {
            v.retain(|x| x.is_finite());
            v.sort_by(f32::total_cmp);
            v.get(v.len() / 2).copied().unwrap_or(default)
        };
        let [a, b, c, e, w, h] = per_slot;
        Dims {
            len: [
                med(a, d.len[0]),
                med(b, d.len[1]),
                med(c, d.len[2]),
                med(e, d.len[3]),
            ],
            torso_w: med(w, d.torso_w),
            torso_h: med(h, d.torso_h),
        }
    }

    /// Median over the whole replay, once at load. Falls back to defaults for anything never fully tracked.
    pub fn measure(replay: &Replay, player: usize) -> Dims {
        let mut per_slot: [Vec<f32>; 6] = Default::default();
        for frame in 0..replay.frame_count {
            for (slot, l) in per_slot
                .iter_mut()
                .zip(frame_lengths(replay.joints(frame, player)))
            {
                slot.extend(l);
            }
        }
        Self::from_lengths(per_slot)
    }
}

/// Flat disc on the floor facing up, centered on the origin in x/z.
pub fn shadow_mesh() -> CpuMesh {
    let mut vertices = vec![shadow_vertex(0.0, 0.0)];
    let mut indices = Vec::new();
    for i in 0..SHADOW_SEGMENTS {
        let a = i as f32 / SHADOW_SEGMENTS as f32 * std::f32::consts::TAU;
        vertices.push(shadow_vertex(a.cos(), a.sin()));
        let (p, n) = (i as u32 + 1, (i as u32 + 1) % SHADOW_SEGMENTS as u32 + 1);
        // Increasing angle runs clockwise seen from +Y, so the CCW (upward) order is center, next, current
        indices.extend_from_slice(&[0, n, p]);
    }
    finish((vertices, indices))
}

fn shadow_vertex(x: f32, z: f32) -> crate::renderer::model::ModelVertex {
    crate::renderer::model::ModelVertex {
        position: [x * SHADOW_RADIUS, SHADOW_Y, z * SHADOW_RADIUS],
        tex_coords: [x * 0.5 + 0.5, z * 0.5 + 0.5],
        normal: [0.0, 1.0, 0.0],
        tangent: [0.0; 3],
        bitangent: [0.0; 3],
    }
}

// Rotation whose local +Y is `y` and whose local +X is as close to `reference` as the bone allows
fn limb_rotation(y: V, reference: V) -> Quaternion<f32> {
    let y = y.normalize();
    let toward = |r: V| {
        let x = r - y * r.dot(y);
        (x.magnitude2() > 1e-6).then(|| x.normalize())
    };
    // Reference parallel to the bone: fall back to world Z (and X if the bone is along Z too)
    let x = toward(reference)
        .or_else(|| toward(V::unit_z()))
        .or_else(|| toward(V::unit_x()))
        .unwrap_or(V::unit_x());
    Quaternion::from(Matrix3::from_cols(x, y, x.cross(y)))
}

// Torso axes (forward, up, right) from the shoulders and hips
fn torso_axes(j: &Joints) -> Option<(V, V, V)> {
    if ![5, 6, 11, 12].iter().all(|&i| ok(j, i)) {
        return None;
    }
    let up = (pos(&j[5]) + pos(&j[6]) - pos(&j[11]) - pos(&j[12])) / 2.0;
    let right = pos(&j[6]) - pos(&j[5]);
    if up.magnitude2() < 1e-8 {
        return None;
    }
    let y = up.normalize();
    let z = right - y * right.dot(y);
    if z.magnitude2() < 1e-8 {
        return None;
    }
    let z = z.normalize();
    // Y x Z = X: a player standing upright facing +X has forward = +X
    Some((y.cross(z), y, z))
}

// Floor-plane forward for a body whose `a`-to-`b` joints are its left and right sides
fn facing_from(j: &Joints, a: usize, b: usize) -> Option<V> {
    if !(ok(j, a) && ok(j, b)) {
        return None;
    }
    // Y x right = forward
    let v = V::unit_y().cross(pos(&j[b]) - pos(&j[a]));
    let v = V::new(v.x, 0.0, v.z);
    (v.magnitude2() > 1e-6).then(|| v.normalize())
}

/// Per-player orientation memory. Orientation must be continuous in time: a stateless
/// choice (e.g. a +X fallback when a hip drops out) flips whole bodies. Own one per player and
/// `reset` it after a seek so scrubbing does not depend on history.
#[derive(Copy, Clone, Default)]
pub struct BodyOrient {
    // Last good horizontal facing
    facing: Option<V>,
    // Per limb: previous bone direction and rotation, None while the limb was hidden
    limbs: [Option<(V, Quaternion<f32>)>; 8],
    // Bit per part index: twist snapped to the hinge in the last part_transforms call
    snapped: u16,
}

// Max turn per frame of the transported twist toward the hinge twist
// Carried twist further than this from the hinge of a clearly bent joint snaps to it
const SNAP_ANGLE: f32 = 30.0 * std::f32::consts::PI / 180.0;
const TWIST_RATE: f32 = 0.26; // radians per frame (~15 deg)

impl BodyOrient {
    pub fn reset(&mut self) {
        *self = Self::default();
    }

    // Hips, else shoulders, else hold the last facing, else +X (only before any good frame)
    fn update_facing(&mut self, j: &Joints) -> V {
        let f = facing_from(j, 11, 12)
            .or_else(|| facing_from(j, 5, 6))
            .or(self.facing)
            .unwrap_or(V::unit_x());
        self.facing = Some(f);
        f
    }

    // Parallel transport: rotate the previous orientation by the shortest arc between the previous and new
    // bone direction, so twist about the bone never jumps. The hinge plane only initializes it, then nudges it
    // (weighted by how bent the joint is, since the hinge axis is undefined when the limb is straight).
    fn limb(&mut self, i: usize, bone: V, hinge: Option<Hinge>, reference: V) -> Quaternion<f32> {
        let dir = bone.normalize();
        let q = match self.limbs[i] {
            Some((prev_dir, prev_q)) => {
                let mut q = (Quaternion::from_arc(prev_dir, dir, None) * prev_q).normalize();
                if let Some(h) = hinge {
                    let w = smoothstep((h.bend - HINGE_MIN) / (HINGE_FULL - HINGE_MIN));
                    if w > 0.0 {
                        let target = hinge_rotation(dir, h.axis);
                        // Turn toward the hinge twist by at most TWIST_RATE * w per frame: fast enough to
                        // re-converge after a straight spell, slow enough not to read as a flip
                        let angle = 2.0 * q.dot(target).abs().min(1.0).acos();
                        if h.bend > HINGE_FULL && angle > SNAP_ANGLE {
                            // ponytail: one-frame pop by design (user choice). Upgrade path: rate-capped
                            // catch-up with a "converges within N frames" criterion.
                            q = target;
                            self.snapped |= 1 << (2 + i);
                        } else if angle > 1e-4 {
                            q = q
                                .slerp(target, (TWIST_RATE * w / angle).min(1.0))
                                .normalize();
                        }
                    }
                }
                q
            }
            None => match hinge.filter(|h| h.bend >= HINGE_MIN) {
                Some(h) => hinge_rotation(dir, h.axis),
                None => limb_rotation(dir, reference),
            },
        };
        self.limbs[i] = Some((dir, q));
        q
    }
}

// Bend angles (radians) where the hinge pole vector starts to count and where it counts fully
const HINGE_MIN: f32 = 8.0 * std::f32::consts::PI / 180.0;
const HINGE_FULL: f32 = 30.0 * std::f32::consts::PI / 180.0;

fn smoothstep(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// An elbow's or knee's bend plane: unit normal (the local +Z "right" axis a limb box should take) and bend angle.
#[derive(Copy, Clone, Debug)]
pub struct Hinge {
    pub axis: V,
    pub bend: f32,
}

/// Hinge of limb `i` (index into LIMBS) with its partner segment across the shared joint; None when
/// either segment is untracked or the limb is straight (axis undefined).
pub fn hinge_for(j: &Joints, i: usize) -> Option<Hinge> {
    let seg = |k: usize| {
        let (a, b) = LIMBS[k].joints;
        (ok(j, a) && ok(j, b)).then(|| pos(&j[b]) - pos(&j[a]))
    };
    // i ^ 2 pairs upper arm <-> forearm and thigh <-> shin on the same side
    let (me, other) = (seg(i)?, seg(i ^ 2)?);
    let (upper, lower) = if i & 2 == 0 { (me, other) } else { (other, me) };
    let n = upper.cross(lower);
    if n.magnitude2() < 1e-10 {
        return None;
    }
    // Elbows bend forward, knees backward, so legs flip the normal to keep +Z = body right
    let axis = n.normalize() * if LIMBS[i].leg { -1.0 } else { 1.0 };
    Some(Hinge {
        axis,
        bend: n.magnitude().atan2(upper.dot(lower)),
    })
}

// Rotation with local +Y = `y` and local +Z = `z` projected perpendicular to it
fn hinge_rotation(y: V, z: V) -> Quaternion<f32> {
    let z = (z - y * z.dot(y)).normalize();
    Quaternion::from(Matrix3::from_cols(y.cross(z), y, z))
}

/// Rebuilds `orient` as if playback had run up to (not including) `frame`: a cold start at an arbitrary frame
/// picks a twist that history would not, so scrubbing warms up over the previous 30 frames.
pub fn warm_up(replay: &Replay, player: usize, frame: usize, orient: &mut BodyOrient) {
    orient.reset();
    for f in frame.saturating_sub(30)..frame {
        part_transforms(replay.joints(f, player), orient);
    }
}

fn rigid(position: V, rotation: Quaternion<f32>) -> Transform {
    Transform {
        position,
        rotation,
        scale: 1.0,
    }
}

/// Box transforms for one frame; `None` hides a part whose joints are missing.
// ponytail: a single frame's bone length differs a few cm from the measured median, so box ends
// overlap or gap slightly at the joints. Fine for blocky style; fit lengths per frame if it bothers.
pub fn part_transforms(j: &Joints, orient: &mut BodyOrient) -> [Option<Transform>; PARTS] {
    orient.snapped = 0;
    let torso = torso_axes(j);
    let face = orient.update_facing(j);
    // Upright frame for head and feet: x = facing, y = up, z = right
    let upright = limb_rotation(V::unit_y(), face);
    let arm_ref = torso.map_or(face, |t| t.0);

    let mut out = [None; PARTS];
    out[HEAD] = mean_of(j, &[0, 1, 2, 3, 4]).map(|c| rigid(c, upright));
    if let Some((f, y, z)) = torso {
        let c = (pos(&j[5]) + pos(&j[6]) + pos(&j[11]) + pos(&j[12])) / 4.0;
        out[TORSO] = Some(rigid(c, Quaternion::from(Matrix3::from_cols(f, y, z))));
    }
    for (i, l) in LIMBS.iter().enumerate() {
        let (a, b) = l.joints;
        let bone = pos(&j[b]) - pos(&j[a]);
        if ok(j, a) && ok(j, b) && bone.magnitude2() > 1e-8 {
            let reference = if l.leg { face } else { arm_ref };
            out[2 + i] = Some(rigid(
                (pos(&j[a]) + pos(&j[b])) / 2.0,
                orient.limb(i, bone, hinge_for(j, i), reference),
            ));
        } else {
            orient.limbs[i] = None;
        }
    }
    for (part, ankle) in [(FOOT_L, 15), (FOOT_R, 16)] {
        if ok(j, ankle) {
            out[part] = Some(rigid(pos(&j[ankle]), upright));
        }
    }
    out
}

// Mean position of the listed joints that are tracked
fn mean_of(j: &Joints, ids: &[usize]) -> Option<V> {
    let n = ids.iter().filter(|&&i| ok(j, i)).count();
    let sum = ids
        .iter()
        .filter(|&&i| ok(j, i))
        .map(|&i| pos(&j[i]))
        .sum::<V>();
    (n > 0).then(|| sum / n as f32)
}

/// Blob shadow under the ankles' midpoint (or the one tracked ankle); `None` when neither is.
pub fn shadow_transform(j: &Joints) -> Option<Transform> {
    let c = mean_of(j, &[15, 16])?;
    Some(rigid(
        V::new(c.x, 0.0, c.z),
        Quaternion::new(1.0, 0.0, 0.0, 0.0),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::Rotation;

    const SAMPLE: &[u8] = include_bytes!("../../../../assets/replay/replay.bin");

    // One player, 10 fps, 30 frames; hips (and a marker x in joint 0) only in frames 2 and 25, whole body otherwise absent
    fn gappy() -> Replay {
        let mut joints = vec![[0.0f32; 4]; 30 * JOINTS];
        for f in [2usize, 25] {
            for j in 0..JOINTS {
                joints[f * JOINTS + j] = [f as f32, j as f32, 0.0, 1.0];
            }
        }
        Replay::from_parts(10.0, 30, 1, joints, None)
    }

    #[test]
    fn hold_returns_earlier_frame_within_a_second_and_none_beyond() {
        let r = gappy();
        // frame 2 held at 1.2 s (frame 12, exactly 1.0 s = 10 frames later); frame 13 is past the limit
        assert_eq!(sample_held(&r, 1.2, 0)[0][0], 2.0);
        assert!(sample_held(&r, 1.3, 0).iter().all(|j| j[3] == 0.0));
        // Before any body existed: nothing to hold
        assert!(sample_held(&r, 0.1, 0).iter().all(|j| j[3] == 0.0));
        // Seeking backwards works (stateless) and a live frame is not replaced
        assert_eq!(sample_held(&r, 0.5, 0)[0][0], 2.0);
        assert_eq!(sample_held(&r, 2.5, 0)[0][0], 25.0);
        // Never mixes frames: every joint comes from the same frame
        let j = sample_held(&r, 0.9, 0);
        assert!(j.iter().all(|j| j[0] == 2.0 && j[3] == 1.0));
    }

    // Upright T-pose facing +X (so the player's right is +Z), feet on the floor
    fn t_pose() -> Joints {
        let mut j = [[0.0; 4]; JOINTS];
        let mut set = |i: usize, p: [f32; 3]| j[i] = [p[0], p[1], p[2], 1.0];
        set(0, [0.05, 1.65, 0.0]);
        set(1, [0.05, 1.68, -0.03]);
        set(2, [0.05, 1.68, 0.03]);
        set(3, [0.0, 1.66, -0.08]);
        set(4, [0.0, 1.66, 0.08]);
        set(5, [0.0, 1.45, -0.2]);
        set(6, [0.0, 1.45, 0.2]);
        set(7, [0.0, 1.45, -0.5]);
        set(8, [0.0, 1.45, 0.5]);
        set(9, [0.0, 1.45, -0.77]);
        set(10, [0.0, 1.45, 0.77]);
        set(11, [0.0, 0.95, -0.1]);
        set(12, [0.0, 0.95, 0.1]);
        set(13, [0.0, 0.5, -0.1]);
        set(14, [0.0, 0.5, 0.1]);
        set(15, [0.0, 0.08, -0.1]);
        set(16, [0.0, 0.08, 0.1]);
        j
    }

    fn rot(t: &Transform, v: V) -> V {
        t.rotation.rotate_vector(v)
    }

    #[test]
    fn torso_forward_is_plus_x_and_legs_point_down() {
        let j = t_pose();
        let p = part_transforms(&j, &mut BodyOrient::default());
        let torso = p[TORSO].unwrap();
        assert!((rot(&torso, V::unit_x()) - V::unit_x()).magnitude() < 1e-5);
        assert!((rot(&torso, V::unit_y()) - V::unit_y()).magnitude() < 1e-5);
        // The bone runs hip -> knee, so local +Y points down the leg
        let thigh = p[2 + 4].unwrap();
        assert!((rot(&thigh, V::unit_y()) + V::unit_y()).magnitude() < 1e-5);
        // Feet and head face +X too
        for part in [FOOT_L, HEAD] {
            assert!((rot(&p[part].unwrap(), V::unit_x()) - V::unit_x()).magnitude() < 1e-5);
        }
    }

    #[test]
    fn limb_box_ends_land_on_joints() {
        let j = t_pose();
        let d = Dims::from_frame(&j);
        let p = part_transforms(&j, &mut BodyOrient::default());
        for (i, l) in LIMBS.iter().enumerate() {
            let t = p[2 + i].unwrap();
            let half = V::new(0.0, d.len[i / 2] / 2.0, 0.0);
            let (start, end) = (t.position - rot(&t, half), t.position + rot(&t, half));
            assert!(
                (start - pos(&j[l.joints.0])).magnitude() < 1e-3,
                "limb {i} start"
            );
            assert!(
                (end - pos(&j[l.joints.1])).magnitude() < 1e-3,
                "limb {i} end"
            );
        }
    }

    #[test]
    fn missing_joint_hides_exactly_its_parts() {
        let mut j = t_pose();
        j[7][3] = 0.0; // left elbow: upper arm L and forearm L
        let p = part_transforms(&j, &mut BodyOrient::default());
        let hidden: Vec<usize> = (0..PARTS).filter(|&i| p[i].is_none()).collect();
        assert_eq!(hidden, vec![2, 4]);
        // A shoulder takes the torso and its upper arm with it
        let mut j = t_pose();
        j[5][3] = 0.0;
        let p = part_transforms(&j, &mut BodyOrient::default());
        let hidden: Vec<usize> = (0..PARTS).filter(|&i| p[i].is_none()).collect();
        assert_eq!(hidden, vec![TORSO, 2]);
        // Head needs only one face joint
        let mut j = t_pose();
        (0..4).for_each(|i| j[i][3] = 0.0);
        assert!(part_transforms(&j, &mut BodyOrient::default())[HEAD].is_some());
        j[4][3] = 0.0;
        assert!(part_transforms(&j, &mut BodyOrient::default())[HEAD].is_none());
    }

    #[test]
    fn bone_along_the_reference_does_not_produce_nan() {
        // Forearm pointing straight forward, parallel to the facing reference
        let mut j = t_pose();
        j[9] = [0.5, 1.45, -0.2, 1.0];
        j[7] = [0.2, 1.45, -0.2, 1.0];
        let t = part_transforms(&j, &mut BodyOrient::default())[4].unwrap();
        let q = t.rotation;
        assert!(q.s.is_finite() && q.v.x.is_finite() && (q.magnitude() - 1.0).abs() < 1e-4);
    }

    #[test]
    fn shadow_follows_ankles() {
        let mut j = t_pose();
        let s = shadow_transform(&j).unwrap();
        assert!((s.position - V::new(0.0, 0.0, 0.0)).magnitude() < 1e-6);
        j[15][3] = 0.0;
        assert!((shadow_transform(&j).unwrap().position.z - 0.1).abs() < 1e-6);
        j[16][3] = 0.0;
        assert!(shadow_transform(&j).is_none());
    }

    #[test]
    fn shadow_disc_faces_up() {
        let (v, i) = shadow_mesh();
        assert_eq!(i.len(), SHADOW_SEGMENTS * 3);
        for tri in i.chunks(3) {
            let p = |k: usize| V::from(v[tri[k] as usize].position);
            assert!((p(1) - p(0)).cross(p(2) - p(0)).y > 0.0, "disc faces down");
        }
    }

    #[test]
    fn dims_from_sample_replay_are_human() {
        let r = Replay::parse(SAMPLE).unwrap();
        for player in 0..r.players {
            let d = Dims::measure(&r, player);
            let inside = |v: f32, lo: f32, hi: f32| (lo..=hi).contains(&v);
            assert!(inside(d.len[0], 0.25, 0.40), "upper arm {d:?}");
            assert!(inside(d.len[1], 0.20, 0.35), "forearm {d:?}");
            assert!(inside(d.len[2], 0.35, 0.55), "thigh {d:?}");
            assert!(inside(d.len[3], 0.35, 0.55), "shin {d:?}");
            assert!(d.torso_w > 0.2 && d.torso_h > 0.3, "{d:?}");
        }
    }

    // Quaternion angle between two rotations in degrees
    fn angle(a: Quaternion<f32>, b: Quaternion<f32>) -> f32 {
        2.0 * a.dot(b).abs().min(1.0).acos().to_degrees()
    }

    // Worst rotation delta beyond (bone/facing delta + 20 deg) per [part], with its frame, over one player.
    // `inject` = (frame, part) replaces that part's rotation with a 180 deg twist about its local Y.
    fn flip_scan(
        r: &Replay,
        player: usize,
        inject: Option<(usize, usize)>,
    ) -> ([f32; PARTS], [usize; PARTS], [f32; PARTS], [u32; PARTS]) {
        let (mut max, mut at, mut raw) = ([0.0f32; PARTS], [0usize; PARTS], [0.0f32; PARTS]);
        let mut snaps = [0u32; PARTS];
        let mut orient = BodyOrient::default();
        let mut prev: Option<([Option<Transform>; PARTS], Joints, V)> = None;
        for f in 0..r.frame_count {
            let j = *r.joints(f, player);
            let mut cur = part_transforms(&j, &mut orient);
            if let Some((_, part)) = inject.filter(|&(frame, _)| frame == f)
                && let Some(t) = cur[part].as_mut()
            {
                t.rotation = t.rotation * Quaternion::new(0.0, 0.0, 1.0, 0.0);
            }
            let face = orient.facing.unwrap();
            for (i, n) in snaps.iter_mut().enumerate() {
                *n += (orient.snapped >> i) as u32 & 1;
            }
            for (i, t) in cur.iter().enumerate() {
                let Some(t) = t else { continue };
                let (q, p) = (t.rotation, t.position);
                assert!(
                    [q.s, q.v.x, q.v.y, q.v.z, p.x, p.y, p.z]
                        .iter()
                        .all(|x| x.is_finite()),
                    "NaN p{player} f{f} part{i}"
                );
                assert!(
                    (q.magnitude() - 1.0).abs() < 1e-4,
                    "non-unit q p{player} f{f} part{i}"
                );
                let Some((pt, pj, pface)) = &prev else {
                    continue;
                };
                let Some(pt) = pt[i] else { continue };
                // Snaps to the hinge are intentional pops
                if orient.snapped >> i & 1 == 1 {
                    continue;
                }
                // Allowed turn: how far the bone (limbs) or the facing (everything else) moved, plus slack
                let allowed = if (2..FOOT_L).contains(&i) {
                    let (a, b) = LIMBS[i - 2].joints;
                    if !(ok(pj, a) && ok(pj, b)) {
                        continue;
                    }
                    let dir = |j: &Joints| (pos(&j[b]) - pos(&j[a])).normalize();
                    dir(pj).dot(dir(&j)).clamp(-1.0, 1.0).acos().to_degrees()
                } else {
                    pface.dot(face).clamp(-1.0, 1.0).acos().to_degrees()
                } + 20.0;
                raw[i] = raw[i].max(angle(pt.rotation, q));
                let excess = angle(pt.rotation, q) - allowed;
                if excess > max[i] {
                    max[i] = excess;
                    at[i] = f;
                }
            }
            prev = Some((cur, j, face));
        }
        (max, at, raw, snaps)
    }

    #[test]
    fn sample_replay_rotations_are_unit_finite_and_do_not_flip() {
        let r = Replay::parse(SAMPLE).unwrap();
        for player in 0..r.players {
            let (max, at, raw, snaps) = flip_scan(&r, player, None);
            println!("p{player} max raw delta {raw:?}");
            println!("p{player} snaps per part {snaps:?}");
            for i in 0..PARTS {
                assert!(
                    max[i] <= 0.0,
                    "flip p{player} part {i} frame {} excess {}",
                    at[i],
                    max[i]
                );
            }
        }
    }

    // The checker must not be vacuous: a 180 deg twist at one frame is flagged for every part
    #[test]
    fn flip_checker_catches_injected_180_twist() {
        let r = Replay::parse(SAMPLE).unwrap();
        let f = r.frame_count / 2;
        for part in 0..PARTS {
            let (max, at, _, _) = flip_scan(&r, 0, Some((f, part)));
            assert!(
                max[part] > 100.0 && (at[part] == f || at[part] == f + 1),
                "part {part}: {max:?} {at:?}"
            );
        }
    }

    // Seek (fresh orient at frame n) vs play from 0 to n: max angle per part over 5 frames
    #[test]
    fn seek_matches_playback_within_drift() {
        let r = Replay::parse(SAMPLE).unwrap();
        let mut worst = 0.0f32;
        for player in 0..r.players {
            let mut played = BodyOrient::default();
            let mut targets: Vec<usize> = (1..=5).map(|k| k * (r.frame_count - 1) / 5).collect();
            targets.reverse();
            for f in 0..r.frame_count {
                let a = part_transforms(r.joints(f, player), &mut played);
                if targets.last() == Some(&f) {
                    targets.pop();
                    let mut fresh = BodyOrient::default();
                    warm_up(&r, player, f, &mut fresh);
                    let b = part_transforms(r.joints(f, player), &mut fresh);
                    for i in 0..PARTS {
                        if let (Some(x), Some(y)) = (a[i], b[i]) {
                            let a_ = angle(x.rotation, y.rotation);
                            if a_ > 25.0 {
                                println!("seek p{player} f{f} part{i} {a_} deg");
                            }
                            worst = worst.max(angle(x.rotation, y.rotation));
                        }
                    }
                }
            }
        }
        println!("seek vs play max angle deg: {worst}");
        assert!(worst < 25.0, "{worst}");
    }

    // Loop the clip 10x through one BodyOrient; twist must stay near the reference-method orientation
    #[test]
    fn twist_drift_over_ten_loops_is_bounded() {
        let r = Replay::parse(SAMPLE).unwrap();
        let mut worst = 0.0f32;
        for player in 0..r.players {
            let mut orient = BodyOrient::default();
            for f in (0..10 * r.frame_count).map(|n| n % r.frame_count) {
                let j = r.joints(f, player);
                let out = part_transforms(j, &mut orient);
                for (i, l) in LIMBS.iter().enumerate() {
                    let Some(t) = out[2 + i] else { continue };
                    // Only meaningful where the hinge plane is well defined
                    let Some(h) = hinge_for(j, i).filter(|h| h.bend > HINGE_FULL) else {
                        continue;
                    };
                    let bone = pos(&j[l.joints.1]) - pos(&j[l.joints.0]);
                    let want = hinge_rotation(bone.normalize(), h.axis);
                    let a_ = angle(t.rotation, want);
                    worst = worst.max(a_);
                }
            }
        }
        println!("max twist deviation over 10 loops deg: {worst}");
        assert!(worst < 30.0, "{worst}");
    }
}

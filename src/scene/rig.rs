//! Skinned block humanoid ("Basic Humanoid Block Rig", Trogan305, CC-BY-4.0, see CREDITS.md) posed from COCO-17 joints.
//!
//! `Rig::parse` reads the glTF by hand (serde_json + accessors; no `gltf` crate, which would pull image/fs
//! machinery for one file). `Rig::pose` fills a joint palette per player per frame.
//!
//! Orientation comes from `body::part_transforms` (facing, hinge twist, snap, warm-up on seek). For each driven bone
//! `R_bone_world = R_driver(frame) * R_offset`, `R_offset = R_driver(rest)^-1 * R_bone(rest)`, where the rest drivers
//! come from running `part_transforms` on a COCO pose built from the rig's own rest joints.
// ponytail: loads only this file's skin + meshes (single buffer, no mesh-node transforms, one skin), not a general
// glTF importer.

use anyhow::{Context, Result, bail, ensure};
use cgmath::{InnerSpace, Matrix3, Matrix4, Quaternion, SquareMatrix, Vector3, Vector4};
use serde_json::Value;

use crate::scene::body::{self, BodyOrient, Dims, Joints, PARTS};

type V = Vector3<f32>;
type Q = Quaternion<f32>;

/// Joints in the glTF skin.
pub const BONES: usize = 29;
/// Palette slots in the shader's uniform array (WebGL2 uniform blocks; no storage buffers).
pub const MAX_JOINTS: usize = 32;
/// Column-major 4x4 per joint, ready for the uniform buffer.
pub type Palette = [[[f32; 4]; 4]; MAX_JOINTS];

/// Keep in sync with VertexInput in shaders/skinned.wgsl. Joint indices are floats for WebGL2 attribute compatibility.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct SkinnedVertex {
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub joints: [f32; 4],
    pub weights: [f32; 4],
}

pub fn identity_palette() -> Palette {
    let mut p = [[[0.0; 4]; 4]; MAX_JOINTS];
    for m in &mut p {
        for (i, c) in m.iter_mut().enumerate() {
            c[i] = 1.0;
        }
    }
    p
}

// Orientation sources: the body parts of `body::part_transforms` (indices 0..PARTS) plus the pelvis
const UA_L: usize = 2;
const UA_R: usize = 3;
const FA_L: usize = 4;
const FA_R: usize = 5;
const TH_L: usize = 6;
const TH_R: usize = 7;
const SH_L: usize = 8;
const SH_R: usize = 9;
const PELVIS: usize = PARTS;
const DRIVERS: usize = PARTS + 1;

#[derive(Copy, Clone)]
enum Drive {
    // Driver rotation times the bone's constant offset
    Raw(usize),
    // Slerp between two drivers (a at 0, b at 1), then the offset
    Blend(usize, usize, f32),
    // Keeps its rest rotation relative to the parent bone (hand, thumbs, toes)
    Child,
}

#[derive(Copy, Clone)]
enum At {
    // Hip midpoint of the data, offset by the bone's rest position relative to the hip midpoint
    Hips,
    // On this COCO joint when it is tracked, else like Fk
    Joint(usize),
    // Parent position plus the rest offset, rotated and scaled
    Fk,
}

// One row per skin joint, in skin order (parents before children).
//
// Roles were read off the rest pose, not the names (all generic `Bone.0xx`): world positions of the 29 joints
// (rig_to_court * inverse(inverse_bind), meters, up = +Y) show a spine chain rising from y=0.91 (pelvis) to the
// head at 1.53, two arm chains running out along +Z / -Z at shoulder height ending in a 2-bone thumb, and two leg
// chains at z=+-0.105 from y=0.75 (hip) through 0.42 (knee) to 0.075 (ankle) and a foot bone just below. The
// skinned foot blocks reach from x=-0.09 to +0.16 (toes) and the hands/thumbs sit at +x, so the rig faces +X and
// +Z is its right (X x Y = Z), i.e. COCO odd (left) joints map to the -Z side. A bone's rotation moves the segment
// from it to its child, so the upper arm is driven by the shoulder bone, the forearm by the elbow bone, and so on.
const TABLE: [(&str, Drive, At); BONES] = [
    ("root", Drive::Raw(PELVIS), At::Hips),
    ("pelvis", Drive::Raw(PELVIS), At::Hips),
    (
        "spine",
        Drive::Blend(PELVIS, body::TORSO, 1.0 / 3.0),
        At::Fk,
    ),
    (
        "chest",
        Drive::Blend(PELVIS, body::TORSO, 2.0 / 3.0),
        At::Fk,
    ),
    ("neck", Drive::Blend(body::TORSO, body::HEAD, 0.5), At::Fk),
    ("head", Drive::Raw(body::HEAD), At::Fk),
    ("clavicle_r", Drive::Raw(body::TORSO), At::Fk),
    ("shoulder_r", Drive::Raw(UA_R), At::Joint(6)),
    ("elbow_r", Drive::Raw(FA_R), At::Joint(8)),
    ("wrist_r", Drive::Child, At::Joint(10)),
    ("thumb_r", Drive::Child, At::Fk),
    ("thumb_tip_r", Drive::Child, At::Fk),
    ("clavicle_l", Drive::Raw(body::TORSO), At::Fk),
    ("shoulder_l", Drive::Raw(UA_L), At::Joint(5)),
    ("elbow_l", Drive::Raw(FA_L), At::Joint(7)),
    ("wrist_l", Drive::Child, At::Joint(9)),
    ("thumb_l", Drive::Child, At::Fk),
    ("thumb_tip_l", Drive::Child, At::Fk),
    ("hips_root", Drive::Raw(PELVIS), At::Hips),
    ("hip_pivot_r", Drive::Raw(PELVIS), At::Fk),
    ("hip_r", Drive::Raw(TH_R), At::Joint(12)),
    ("knee_r", Drive::Raw(SH_R), At::Joint(14)),
    ("ankle_r", Drive::Raw(body::FOOT_R), At::Joint(16)),
    ("toe_r", Drive::Child, At::Fk),
    ("hip_pivot_l", Drive::Raw(PELVIS), At::Fk),
    ("hip_l", Drive::Raw(TH_L), At::Joint(11)),
    ("knee_l", Drive::Raw(SH_L), At::Joint(13)),
    ("ankle_l", Drive::Raw(body::FOOT_L), At::Joint(15)),
    ("toe_l", Drive::Child, At::Fk),
];

// Skin indices used for fitting and for building the rest COCO pose: COCO joint -> bone
const COCO_BONE: [(usize, usize); 12] = [
    (5, 13),
    (6, 7),
    (7, 14),
    (8, 8),
    (9, 15),
    (10, 9),
    (11, 25),
    (12, 20),
    (13, 26),
    (14, 21),
    (15, 27),
    (16, 22),
];
const HEAD_BONE: usize = 5;
// Limits on the block lengthening, so a bad joint cannot blow a block up
const STRETCH: (f32, f32) = (0.5, 2.0);

pub struct Rig {
    pub vertices: Vec<SkinnedVertex>,
    pub indices: Vec<u32>,
    pub mesh_count: usize,
    parent: [Option<usize>; BONES],
    // Rest world position and rotation of every bone, in court meters (rig_to_court baked in)
    rest_t: [V; BONES],
    rest_q: [Q; BONES],
    rest_drivers: [Q; DRIVERS],
    // Per bone: constant offset (Raw/Blend) or rest rotation relative to the parent (Child)
    offset: [Q; BONES],
    // Per bone: the data-tracked child that its block should reach (upper arm -> elbow, thigh -> knee, ...)
    reach: [Option<usize>; BONES],
    // Rest hip-to-shoulder + thigh + shin, for fitting the rig to a player's `Dims`
    rest_len: f32,
}

/// Per-player pose state: orientation memory, last known driver rotations and the fitted rig scale.
#[derive(Copy, Clone)]
pub struct PlayerRig {
    pub orient: BodyOrient,
    last: [Q; DRIVERS],
    // Per bone: last block lengthening computed from two tracked joints (1.0 until then)
    stretch: [f32; BONES],
    scale: f32,
}

fn floats(v: &Value) -> Vec<f32> {
    v.as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_f64())
                .map(|x| x as f32)
                .collect()
        })
        .unwrap_or_default()
}

fn mat4(a: &[f32]) -> Result<Matrix4<f32>> {
    let a: &[f32; 16] = a.try_into().context("matrix needs 16 numbers")?;
    Ok(Matrix4::from(
        *bytemuck::cast_ref::<[f32; 16], [[f32; 4]; 4]>(a),
    ))
}

fn node_matrix(n: &Value) -> Result<Matrix4<f32>> {
    if n.get("matrix").is_some() {
        return mat4(&floats(&n["matrix"]));
    }
    let t = floats(&n["translation"]);
    let r = floats(&n["rotation"]);
    let s = floats(&n["scale"]);
    let t = if t.len() == 3 {
        V::new(t[0], t[1], t[2])
    } else {
        V::new(0.0, 0.0, 0.0)
    };
    let r = if r.len() == 4 {
        Q::new(r[3], r[0], r[1], r[2])
    } else {
        Q::new(1.0, 0.0, 0.0, 0.0)
    };
    let s = if s.len() == 3 {
        V::new(s[0], s[1], s[2])
    } else {
        V::new(1.0, 1.0, 1.0)
    };
    Ok(Matrix4::from_translation(t)
        * Matrix4::from(r)
        * Matrix4::from_nonuniform_scale(s.x, s.y, s.z))
}

// Accessor `i` as flat f32 values plus the component count (ints are exact below 2^24; normalized ints are scaled)
fn accessor(g: &Value, bin: &[u8], i: usize) -> Result<(Vec<f32>, usize)> {
    let a = &g["accessors"][i];
    ensure!(!a.is_null(), "accessor {i} missing");
    let count = a["count"].as_u64().context("accessor count")? as usize;
    let comps = match a["type"].as_str() {
        Some("SCALAR") => 1,
        Some("VEC2") => 2,
        Some("VEC3") => 3,
        Some("VEC4") => 4,
        Some("MAT4") => 16,
        t => bail!("accessor type {t:?}"),
    };
    let ct = a["componentType"].as_u64().context("componentType")?;
    let (size, norm) = match ct {
        5126 | 5125 => (4, 1.0),
        5123 => (2, 65535.0),
        5121 => (1, 255.0),
        _ => bail!("component type {ct}"),
    };
    let norm = if a["normalized"].as_bool().unwrap_or(false) {
        norm
    } else {
        1.0
    };
    let bv = &g["bufferViews"][a["bufferView"].as_u64().context("bufferView")? as usize];
    ensure!(!bv.is_null(), "bufferView missing");
    let start = (bv["byteOffset"].as_u64().unwrap_or(0) as usize)
        .checked_add(a["byteOffset"].as_u64().unwrap_or(0) as usize)
        .context("accessor offset overflow")?;
    let stride = bv["byteStride"]
        .as_u64()
        .map_or(comps * size, |s| s as usize);
    // Reject a count the buffer cannot hold before allocating for it
    ensure!(
        count <= bin.len(),
        "accessor count {count} exceeds the buffer"
    );
    let mut out = Vec::with_capacity(count * comps);
    for k in 0..count {
        for c in 0..comps {
            let at = k
                .checked_mul(stride)
                .and_then(|o| o.checked_add(start)?.checked_add(c * size))
                .context("accessor offset overflow")?;
            let raw = bin
                .get(at..at.checked_add(size).context("accessor offset overflow")?)
                .context("accessor out of range")?;
            out.push(
                match ct {
                    5126 => f32::from_le_bytes(raw.try_into()?),
                    5125 => u32::from_le_bytes(raw.try_into()?) as f32,
                    5123 => u16::from_le_bytes(raw.try_into()?) as f32,
                    _ => raw[0] as f32,
                } / norm,
            );
        }
    }
    Ok((out, comps))
}

fn world_matrix(nodes: &[Value], parent: &[Option<usize>], mut i: usize) -> Result<Matrix4<f32>> {
    let mut m = node_matrix(nodes.get(i).context("node index")?)?;
    // A parent cycle in corrupt input would loop forever
    for _ in 0..nodes.len() {
        let Some(p) = parent[i] else { return Ok(m) };
        m = node_matrix(&nodes[p])? * m;
        i = p;
    }
    bail!("node hierarchy has a cycle")
}

fn rotation_of(m: &Matrix4<f32>) -> Q {
    let c = |v: Vector4<f32>| v.truncate().normalize();
    Q::from(Matrix3::from_cols(c(m.x), c(m.y), c(m.z)))
}

// Hips frame from the hip line and world up: x = forward, y = up, z = right
fn pelvis_frame(j: &Joints) -> Option<Q> {
    if j[11][3] <= 0.0 || j[12][3] <= 0.0 {
        return None;
    }
    let (l, r) = (
        V::new(j[11][0], j[11][1], j[11][2]),
        V::new(j[12][0], j[12][1], j[12][2]),
    );
    let y = V::unit_y();
    let z = r - l;
    let z = z - y * z.dot(y);
    (z.magnitude2() > 1e-8).then(|| {
        let z = z.normalize();
        Q::from(Matrix3::from_cols(y.cross(z), y, z))
    })
}

fn drivers_of(j: &Joints, orient: &mut BodyOrient) -> [Option<Q>; DRIVERS] {
    let mut d = [None; DRIVERS];
    for (d, t) in d.iter_mut().zip(body::part_transforms(j, orient)) {
        *d = t.map(|t| t.rotation);
    }
    d[PELVIS] = pelvis_frame(j);
    d
}

fn blend(d: &[Q; DRIVERS], drive: Drive) -> Option<Q> {
    match drive {
        Drive::Raw(i) => Some(d[i]),
        Drive::Blend(a, b, t) => Some(d[a].slerp(d[b], t)),
        Drive::Child => None,
    }
}

impl Rig {
    /// `gltf` = scene.gltf, `bin` = the one buffer it references.
    pub fn parse(gltf: &[u8], bin: &[u8]) -> Result<Rig> {
        let g: Value = serde_json::from_slice(gltf).context("glTF json")?;
        ensure!(
            g["buffers"].as_array().is_some_and(|b| b.len() == 1),
            "expected one buffer"
        );
        let nodes = g["nodes"].as_array().context("nodes")?;
        let mut node_parent = vec![None; nodes.len()];
        for (i, n) in nodes.iter().enumerate() {
            for c in n["children"].as_array().into_iter().flatten() {
                let c = c.as_u64().context("child")? as usize;
                *node_parent.get_mut(c).context("child index out of range")? = Some(i);
            }
        }
        ensure!(!g["skins"][0].is_null(), "no skin");
        let skin = &g["skins"][0];
        let joint_nodes: Vec<usize> = skin["joints"]
            .as_array()
            .context("skin joints")?
            .iter()
            .map(|j| j.as_u64().map(|j| j as usize).context("joint index"))
            .collect::<Result<_>>()?;
        ensure!(
            joint_nodes.iter().all(|&j| j < nodes.len()),
            "joint node out of range"
        );
        ensure!(
            joint_nodes.len() == BONES,
            "expected {BONES} joints, got {}",
            joint_nodes.len()
        );
        let (ibm, _) = accessor(
            &g,
            bin,
            skin["inverseBindMatrices"].as_u64().context("ibm")? as usize,
        )?;
        ensure!(
            ibm.len() == BONES * 16,
            "expected {BONES} inverse bind matrices"
        );
        let ibm = ibm.chunks(16).map(mat4).collect::<Result<Vec<_>>>()?;

        // Joint 0 (a childless root with an identity bind) is inconsistent in this file; joint 1 is not.
        // world * inverse_bind = the node chain above the armature = the 0.1313 scale and the axis change.
        let rig_to_court = world_matrix(nodes, &node_parent, joint_nodes[1])? * ibm[1];

        let mut parent = [None; BONES];
        let (mut rest_t, mut rest_q) = (
            [V::new(0.0, 0.0, 0.0); BONES],
            [Q::new(1.0, 0.0, 0.0, 0.0); BONES],
        );
        for k in 0..BONES {
            parent[k] =
                node_parent[joint_nodes[k]].and_then(|p| joint_nodes.iter().position(|&j| j == p));
            let m = rig_to_court * ibm[k].invert().context("singular inverse bind")?;
            rest_t[k] = m.w.truncate();
            rest_q[k] = rotation_of(&m);
        }

        // Meshes merged into one buffer, moved into court meters once
        let normal_rot = Matrix3::from_cols(
            rig_to_court.x.truncate().normalize(),
            rig_to_court.y.truncate().normalize(),
            rig_to_court.z.truncate().normalize(),
        );
        let (mut vertices, mut indices) = (Vec::new(), Vec::new());
        let meshes = g["meshes"].as_array().context("meshes")?;
        for prim in meshes
            .iter()
            .flat_map(|m| m["primitives"].as_array().into_iter().flatten())
        {
            ensure!(prim["mode"].as_u64().unwrap_or(4) == 4, "triangles only");
            let attr = |name: &str| -> Result<Vec<f32>> {
                Ok(accessor(
                    &g,
                    bin,
                    prim["attributes"][name]
                        .as_u64()
                        .with_context(|| name.to_string())? as usize,
                )?
                .0)
            };
            let (pos, nor, jts, wts) = (
                attr("POSITION")?,
                attr("NORMAL")?,
                attr("JOINTS_0")?,
                attr("WEIGHTS_0")?,
            );
            let base = vertices.len() as u32;
            ensure!(
                pos.len() % 3 == 0
                    && nor.len() == pos.len()
                    && jts.len() / 4 == pos.len() / 3
                    && wts.len() == jts.len()
                    && jts.len() % 4 == 0,
                "mismatched vertex attribute counts"
            );
            for i in 0..pos.len() / 3 {
                let p =
                    rig_to_court * Vector4::new(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2], 1.0);
                let n = normal_rot * V::new(nor[3 * i], nor[3 * i + 1], nor[3 * i + 2]);
                let mut w = [wts[4 * i], wts[4 * i + 1], wts[4 * i + 2], wts[4 * i + 3]];
                let j = [jts[4 * i], jts[4 * i + 1], jts[4 * i + 2], jts[4 * i + 3]];
                let sum: f32 = w.iter().sum();
                ensure!(sum > 0.0, "vertex without weights");
                w.iter_mut().for_each(|x| *x /= sum);
                ensure!(
                    j.iter()
                        .zip(&w)
                        .all(|(&j, &w)| w == 0.0 || (j >= 0.0 && (j as usize) < BONES)),
                    "joint index out of range"
                );
                vertices.push(SkinnedVertex {
                    position: [p.x, p.y, p.z],
                    normal: n.normalize().into(),
                    joints: j,
                    weights: w,
                });
            }
            let (idx, _) = accessor(
                &g,
                bin,
                prim["indices"].as_u64().context("indices")? as usize,
            )?;
            indices.extend(idx.iter().map(|&i| base.wrapping_add(i as u32)));
        }
        ensure!(
            indices.iter().all(|&i| (i as usize) < vertices.len()),
            "index out of range"
        );

        // Rest drivers: part_transforms on the rig's own rest joints (rest pose in -> identity deltas out)
        let rest_joints = Self::rest_joints(&rest_t);
        let rest = drivers_of(&rest_joints, &mut BodyOrient::default());
        let mut rest_drivers = [Q::new(1.0, 0.0, 0.0, 0.0); DRIVERS];
        for (r, d) in rest_drivers.iter_mut().zip(rest) {
            *r = d.context("rest pose is missing a body part")?;
        }
        let mut offset = [Q::new(1.0, 0.0, 0.0, 0.0); BONES];
        for k in 0..BONES {
            offset[k] = match blend(&rest_drivers, TABLE[k].1) {
                Some(d) => d.conjugate() * rest_q[k],
                None => {
                    rest_q[parent[k].context("child bone without parent")?].conjugate() * rest_q[k]
                }
            };
        }
        let mut reach = [None; BONES];
        for c in 0..BONES {
            if let (Some(p), At::Joint(_)) = (parent[c], TABLE[c].2)
                && matches!(TABLE[p].2, At::Joint(_))
            {
                reach[p] = Some(c);
            }
        }
        let mid = |a: usize, b: usize| (rest_t[a] + rest_t[b]) / 2.0;
        let rest_len = (mid(7, 13) - mid(20, 25)).magnitude()
            + (rest_t[20] - rest_t[21]).magnitude()
            + (rest_t[21] - rest_t[22]).magnitude();
        Ok(Rig {
            vertices,
            indices,
            mesh_count: meshes.len(),
            parent,
            rest_t,
            rest_q,
            rest_drivers,
            offset,
            reach,
            rest_len,
        })
    }

    // COCO-17 (conf 1) from the rest bone positions; the face joints all sit on the head bone
    fn rest_joints(rest_t: &[V; BONES]) -> Joints {
        let mut j = [[0.0; 4]; crate::replay::JOINTS];
        let mut set = |i: usize, p: V| j[i] = [p.x, p.y, p.z, 1.0];
        (0..5).for_each(|i| set(i, rest_t[HEAD_BONE]));
        for (coco, bone) in COCO_BONE {
            set(coco, rest_t[bone]);
        }
        j
    }

    /// COCO joints of the rig's rest pose (a T-pose facing +X, feet on the floor).
    pub fn rest_pose(&self) -> Joints {
        Self::rest_joints(&self.rest_t)
    }

    pub fn bone_role(k: usize) -> &'static str {
        TABLE[k].0
    }

    /// Skin position of the bone `k` in the rest pose (court meters).
    pub fn rest_position(&self, k: usize) -> V {
        self.rest_t[k]
    }

    /// Joint palette for one player and frame; `false` (palette untouched) when both hips are missing (hide the player).
    /// Positions: data joints where tracked, otherwise forward kinematics; no allocation.
    pub fn pose(&self, joints: &Joints, player: &mut PlayerRig, out: &mut Palette) -> bool {
        for (last, d) in player
            .last
            .iter_mut()
            .zip(drivers_of(joints, &mut player.orient))
        {
            if let Some(d) = d {
                *last = d;
            }
        }
        let at = |i: usize| V::new(joints[i][0], joints[i][1], joints[i][2]);
        let Some(hips) = hip_center(joints) else {
            return false;
        };
        let rest_hips = (self.rest_t[20] + self.rest_t[25]) / 2.0;
        let s = player.scale;
        let mut mats = [Matrix3::from_value(1.0); BONES];
        let (mut rot, mut pos) = (
            [Q::new(1.0, 0.0, 0.0, 0.0); BONES],
            [V::new(0.0, 0.0, 0.0); BONES],
        );
        for k in 0..BONES {
            let (_, drive, place) = TABLE[k];
            let parent = self.parent[k];
            rot[k] = match (blend(&player.last, drive), parent) {
                (Some(d), _) => d * self.offset[k],
                (None, Some(p)) => rot[p] * self.offset[k],
                (None, None) => self.rest_q[k],
            };
            // v' = pos + s * rot * rest_q^-1 * stretch * (v - rest_t), where stretch lengthens the block along the
            // rest bone axis by (data bone length / rig bone length) so it reaches the data child joint. The rig's
            // limbs are shorter than a measured player's per frame, which would otherwise open gaps at elbows and knees.
            // Only two tracked joints update the factor (a conf-0 joint is position 0,0,0 and must never be a
            // target); otherwise the last valid factor holds.
            let mut a = Matrix3::from(rot[k] * self.rest_q[k].conjugate()) * s;
            if let Some(c) = self.reach[k] {
                if let (At::Joint(j), At::Joint(jc)) = (place, TABLE[c].2)
                    && joints[j][3] > 0.0
                    && joints[jc][3] > 0.0
                {
                    let len = (at(jc) - at(j)).magnitude()
                        / (s * (self.rest_t[c] - self.rest_t[k]).magnitude());
                    if len.is_finite() {
                        player.stretch[k] = len.clamp(STRETCH.0, STRETCH.1);
                    }
                }
                let u = (self.rest_t[c] - self.rest_t[k]).normalize();
                let e = player.stretch[k] - 1.0;
                // I + (k - 1) u u^T
                a = a * Matrix3::from_cols(
                    V::unit_x() + u * (e * u.x),
                    V::unit_y() + u * (e * u.y),
                    V::unit_z() + u * (e * u.z),
                );
            }
            mats[k] = a;
            // Forward kinematics follows the parent's (stretched) frame, so an untracked joint stays where its block ends
            pos[k] = match (place, parent) {
                (At::Joint(j), _) if joints[j][3] > 0.0 => at(j),
                (At::Hips, _) | (_, None) => {
                    hips + rot[k] * (self.rest_q[k].conjugate() * (self.rest_t[k] - rest_hips)) * s
                }
                (_, Some(p)) => pos[p] + mats[p] * (self.rest_t[k] - self.rest_t[p]),
            };
            let t = pos[k] - a * self.rest_t[k];
            out[k] = [
                [a.x.x, a.x.y, a.x.z, 0.0],
                [a.y.x, a.y.y, a.y.z, 0.0],
                [a.z.x, a.z.y, a.z.z, 0.0],
                [t.x, t.y, t.z, 1.0],
            ];
        }
        true
    }
}

// Midpoint of the hips. With one hip occluded (common: the tracker drops one while the body turns) the visible one is
// shifted by the shoulders' offset to the same side, so the player stays drawn; None when both are missing.
pub fn hip_center(joints: &Joints) -> Option<V> {
    let at = |i: usize| V::new(joints[i][0], joints[i][1], joints[i][2]);
    let seen = |i: usize| joints[i][3] > 0.0;
    match (seen(11), seen(12)) {
        (true, true) => Some((at(11) + at(12)) / 2.0),
        (false, false) => None,
        (left, _) => {
            let (hip, shoulder) = if left { (11, 5) } else { (12, 6) };
            let shift = if seen(5) && seen(6) {
                (at(5) + at(6)) / 2.0 - at(shoulder)
            } else {
                V::new(0.0, 0.0, 0.0)
            };
            Some(at(hip) + shift)
        }
    }
}

impl PlayerRig {
    /// Rig scaled so its hip-to-shoulder + thigh + shin equals the player's measured ones.
    pub fn new(rig: &Rig, dims: &Dims) -> Self {
        Self {
            orient: BodyOrient::default(),
            last: rig.rest_drivers,
            stretch: [1.0; BONES],
            scale: (dims.torso_h + dims.len[2] + dims.len[3]) / rig.rest_len,
        }
    }

    pub fn scale(&self) -> f32 {
        self.scale
    }

    #[cfg(test)]
    fn with_scale(mut self, scale: f32) -> Self {
        self.scale = scale;
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay::Replay;
    use cgmath::{Deg, Rotation3};

    const GLTF: &[u8] = include_bytes!("../../assets/models/humanoid/scene.gltf");
    const BIN: &[u8] = include_bytes!("../../assets/models/humanoid/scene.bin");
    const SAMPLE: &[u8] = include_bytes!("../../assets/replay/replay.bin");

    fn rig() -> Rig {
        Rig::parse(GLTF, BIN).unwrap()
    }

    // Linear blend skinning of a rest vertex position / normal on the CPU
    fn skin(p: &Palette, v: &SkinnedVertex) -> (V, V) {
        let (mut pos, mut nor) = (V::new(0.0, 0.0, 0.0), V::new(0.0, 0.0, 0.0));
        for (&j, &w) in v.joints.iter().zip(&v.weights) {
            if w == 0.0 {
                continue;
            }
            let m = &p[j as usize];
            let (x, n) = (V::from(v.position), V::from(v.normal));
            let col = |c: usize| V::new(m[c][0], m[c][1], m[c][2]);
            pos += (col(0) * x.x + col(1) * x.y + col(2) * x.z + col(3)) * w;
            nor += (col(0) * n.x + col(1) * n.y + col(2) * n.z) * w;
        }
        (pos, nor.normalize())
    }

    fn posed(rig: &Rig, joints: &Joints, scale: f32) -> Palette {
        let mut player = PlayerRig::new(rig, &Dims::default()).with_scale(scale);
        let mut out = identity_palette();
        assert!(rig.pose(joints, &mut player, &mut out));
        out
    }

    // Applies a bone's palette matrix to a point
    fn apply(m: &[[f32; 4]; 4], p: V) -> V {
        let col = |c: usize| V::new(m[c][0], m[c][1], m[c][2]);
        col(0) * p.x + col(1) * p.y + col(2) * p.z + col(3)
    }

    fn rotate_yaw(j: &Joints, around: V, deg: f32) -> Joints {
        let q = Q::from_angle_y(Deg(deg));
        let mut out = *j;
        for p in out.iter_mut() {
            let r = q * (V::new(p[0], p[1], p[2]) - around) + around;
            *p = [r.x, r.y, r.z, p[3]];
        }
        out
    }

    #[test]
    fn parses_blocks_joints_and_weights() {
        let r = rig();
        assert_eq!(r.mesh_count, 18);
        assert_eq!(r.vertices.len(), 480);
        assert_eq!(r.indices.len() % 3, 0);
        for v in &r.vertices {
            assert!((v.weights.iter().sum::<f32>() - 1.0).abs() < 1e-4);
            for (&j, &w) in v.joints.iter().zip(&v.weights) {
                assert!(w == 0.0 || (j as usize) < BONES);
            }
        }
    }

    #[test]
    fn rest_pose_is_human_sized_and_on_the_floor() {
        let r = rig();
        let ys = r.vertices.iter().map(|v| v.position[1]);
        let (lo, hi) = (
            ys.clone().fold(f32::MAX, f32::min),
            ys.fold(f32::MIN, f32::max),
        );
        assert!((hi - lo - 1.76).abs() < 0.03, "height {}", hi - lo);
        assert!(lo.abs() < 0.02, "feet at {lo}");
    }

    #[test]
    fn bone_table_covers_every_coco_limb_joint_once_and_matches_the_rest_geometry() {
        let r = rig();
        let mut seen = [0; 17];
        for (_, _, at) in TABLE {
            if let At::Joint(j) = at {
                seen[j] += 1;
            }
        }
        assert!((5..17).all(|j| seen[j] == 1), "{seen:?}");
        // COCO bone pairs agree with the table's bone roles: right (even) joints on +Z, left on -Z
        for (coco, bone) in COCO_BONE {
            let z = r.rest_t[bone].z;
            assert_eq!(
                z > 0.0,
                coco % 2 == 0,
                "{} for COCO {coco}",
                Rig::bone_role(bone)
            );
            assert!(
                matches!(TABLE[bone].2, At::Joint(j) if j == coco),
                "{}",
                Rig::bone_role(bone)
            );
        }
        // Heights: ankle < knee < hip < pelvis < chest < neck < head, shoulders at neck level, both sides
        let y = |k: usize| r.rest_t[k].y;
        for (ankle, knee, hip) in [(22, 21, 20), (27, 26, 25)] {
            assert!(y(ankle) < y(knee) && y(knee) < y(hip) && y(hip) < y(1));
        }
        assert!(y(1) < y(2) && y(2) < y(3) && y(3) < y(4) && y(4) < y(5));
        assert!((y(7) - y(13)).abs() < 1e-3 && (y(9) - y(7)).abs() < 0.05 && y(7) > y(3));
    }

    #[test]
    fn rest_pose_in_gives_rest_vertices_out() {
        let r = rig();
        let palette = posed(&r, &r.rest_pose(), 1.0);
        for v in &r.vertices {
            let (p, n) = skin(&palette, v);
            assert!((p - V::from(v.position)).magnitude() < 1e-3);
            assert!(n.dot(V::from(v.normal)) > 0.999);
        }
    }

    #[test]
    fn faces_the_data_and_left_right_are_not_mirrored() {
        let r = rig();
        let rest = r.rest_pose();
        // Chest block = vertices bound to bone 2 whose rest normal points +X
        let chest: Vec<&SkinnedVertex> = r
            .vertices
            .iter()
            .filter(|v| v.joints[0] == 2.0 && v.weights[0] == 1.0 && v.normal[0] > 0.99)
            .collect();
        assert!(!chest.is_empty());
        let hips = (V::from([rest[11][0], rest[11][1], rest[11][2]])
            + V::from([rest[12][0], rest[12][1], rest[12][2]]))
            / 2.0;
        for (yaw, want) in [
            (0.0, V::unit_x()),
            (90.0, -V::unit_z()),
            (180.0, -V::unit_x()),
        ] {
            let j = rotate_yaw(&rest, hips, yaw);
            let palette = posed(&r, &j, 1.0);
            for v in &chest {
                let (_, n) = skin(&palette, v);
                assert!(n.dot(want) > 5.0f32.to_radians().cos(), "yaw {yaw}: {n:?}");
            }
        }
        // Raise the COCO left wrist (9) forward-left: the rig's left (-Z) wrist bone follows, the right one stays
        let mut j = rest;
        j[9] = [0.4, 1.5, -0.5, 1.0];
        let palette = posed(&r, &j, 1.0);
        let left = apply(&palette[15], r.rest_t[15]);
        let right = apply(&palette[9], r.rest_t[9]);
        assert!((left - V::new(0.4, 1.5, -0.5)).magnitude() < 1e-3);
        assert!((right - r.rest_t[9]).magnitude() < 1e-3);
    }

    #[test]
    fn sample_replay_joints_land_on_data_and_palettes_are_finite() {
        let rep = Replay::parse(SAMPLE).unwrap();
        let r = rig();
        // (wrist, elbow, knee, ankle) x (sum, max) of rig-joint-to-data error, and of the block-end gap:
        // the parent block's far end (palette of the parent applied to the child's rest position) vs the data child
        let (mut joint_err, mut end_err, mut n) = ([[0.0f32; 2]; 4], [[0.0f32; 2]; 4], [0u32; 4]);
        // (coco joint, bone, parent bone, name)
        let mapped = [
            (9, 15, 14),
            (7, 14, 13),
            (13, 26, 25),
            (15, 27, 26),
            (10, 9, 8),
            (8, 8, 7),
            (14, 21, 20),
            (16, 22, 21),
        ];
        for p in 0..rep.players {
            let mut player = PlayerRig::new(&r, &Dims::measure(&rep, p));
            let mut palette = identity_palette();
            for f in 0..rep.frame_count {
                let j = rep.joints(f, p);
                let shown = r.pose(j, &mut player, &mut palette);
                assert_eq!(shown, j[11][3] > 0.0 || j[12][3] > 0.0);
                assert!(
                    palette.iter().flatten().flatten().all(|x| x.is_finite()),
                    "NaN p{p} f{f}"
                );
                if !shown {
                    continue;
                }
                for (i, &(coco, bone, parent)) in mapped.iter().enumerate() {
                    if j[coco][3] < 1.0 {
                        continue;
                    }
                    let data = V::new(j[coco][0], j[coco][1], j[coco][2]);
                    let e = (apply(&palette[bone], r.rest_t[bone]) - data).magnitude();
                    assert!(e < 0.02, "p{p} f{f} coco {coco}: {e}");
                    let slot = i % 4;
                    joint_err[slot][0] += e;
                    joint_err[slot][1] = joint_err[slot][1].max(e);
                    // The gap only means something when the parent's own joint is tracked too
                    let At::Joint(pj) = TABLE[parent].2 else {
                        continue;
                    };
                    if j[pj][3] < 1.0 {
                        continue;
                    }
                    let g = (apply(&palette[parent], r.rest_t[bone]) - data).magnitude();
                    assert!(g < 0.02, "p{p} f{f} coco {coco} gap {g}");
                    end_err[slot][0] += g;
                    end_err[slot][1] = end_err[slot][1].max(g);
                    n[slot] += 1;
                }
            }
        }
        for (i, name) in ["wrist", "elbow", "knee", "ankle"].iter().enumerate() {
            let c = n[i].max(1) as f32;
            println!(
                "{name}: joint err mean {:.4} max {:.4} m; block-end gap mean {:.4} max {:.4} m ({} samples)",
                joint_err[i][0] / c,
                joint_err[i][1],
                end_err[i][0] / c,
                end_err[i][1],
                n[i]
            );
        }
    }

    #[test]
    fn missing_hip_hides_and_missing_elbow_falls_back_to_fk() {
        let r = rig();
        let mut j = r.rest_pose();
        j[8][3] = 0.0;
        let palette = posed(&r, &j, 1.0);
        assert!(palette.iter().flatten().flatten().all(|x| x.is_finite()));
        let mut player = PlayerRig::new(&r, &Dims::default());
        // One occluded hip still draws the player, near where both hips would put it
        let both = r.pose(&j, &mut player, &mut identity_palette());
        j[11][3] = 0.0;
        let mut palette = identity_palette();
        assert!(both && r.pose(&j, &mut player, &mut palette));
        assert!(palette.iter().flatten().flatten().all(|x| x.is_finite()));
        j[12][3] = 0.0;
        assert!(!r.pose(&j, &mut player, &mut identity_palette()));
    }

    // Regression: P0 vanished at t = 1.31 s because the left hip had conf 0
    #[test]
    fn sample_replay_players_are_drawn_whenever_a_hip_is_tracked() {
        let (r, replay) = (rig(), Replay::parse(SAMPLE).unwrap());
        for p in 0..replay.players {
            let mut player = PlayerRig::new(&r, &crate::scene::body::Dims::measure(&replay, p));
            let mut palette = identity_palette();
            let mut one_hip = 0;
            for f in 0..replay.frame_count {
                let j = replay.joints(f, p);
                let drawn = r.pose(j, &mut player, &mut palette);
                assert_eq!(
                    drawn,
                    j[11][3] > 0.0 || j[12][3] > 0.0,
                    "player {p} frame {f}"
                );
                if drawn {
                    assert!(palette.iter().flatten().flatten().all(|x| x.is_finite()));
                    one_hip += (j[11][3] <= 0.0 || j[12][3] <= 0.0) as usize;
                }
            }
            println!("player {p}: {one_hip} one-hip frames");
        }
    }

    // A one-hip frame must not jump the body: compare with the midpoint of its neighbours' centers.
    #[test]
    fn one_hip_center_stays_near_neighbouring_frames() {
        let replay = Replay::parse(SAMPLE).unwrap();
        let mut worst = 0.0f32;
        let mut base = 0.0f32;
        let mut n = 0;
        for p in 0..replay.players {
            for f in 1..replay.frame_count - 1 {
                let j = replay.joints(f, p);
                if j[11][3] > 0.0 && j[12][3] > 0.0 {
                    let c = |f| hip_center(replay.joints(f, p));
                    if let (Some(c), Some(a), Some(b)) = (c(f), c(f - 1), c(f + 1)) {
                        base = base.max((c - (a + b) / 2.0).magnitude());
                    }
                    continue;
                }
                n += 1;
                let c = |f| hip_center(replay.joints(f, p));
                if let (Some(c), Some(a), Some(b)) = (c(f), c(f - 1), c(f + 1)) {
                    worst = worst.max((c - (a + b) / 2.0).magnitude());
                }
            }
        }
        println!("one-hip frames {n}: worst deviation {worst} m (two-hip baseline {base} m)");
        assert!(worst < base + 0.06, "{worst} vs baseline {base}");
    }

    // Stretch is I + (k-1) u u^T along the rest bone axis; the shader transforms normals by it (no inverse transpose).
    // Max angle between that and the exact normal (S^-1 n) at k = 2 over every vertex of every stretched bone.
    #[test]
    fn stretch_normal_error_is_small() {
        let r = rig();
        let mut worst = 0.0f32;
        for (k, c) in r.reach.iter().enumerate() {
            let Some(c) = *c else { continue };
            let u = (r.rest_t[c] - r.rest_t[k]).normalize();
            for v in r
                .vertices
                .iter()
                .filter(|v| v.joints[0] as usize == k && v.weights[0] == 1.0)
            {
                let n = V::from(v.normal);
                let shader = n + u * (n.dot(u));
                let exact = n - u * (n.dot(u) * 0.5);
                worst = worst.max(
                    shader
                        .normalize()
                        .dot(exact.normalize())
                        .clamp(-1.0, 1.0)
                        .acos()
                        .to_degrees(),
                );
            }
        }
        println!("max stretch normal error {worst} deg");
        assert!(worst < 10.0, "{worst}");
    }

    #[test]
    fn corrupt_input_is_an_error_not_a_panic() {
        for cut in [0, 1, 100, BIN.len() / 2, BIN.len() - 1] {
            let _ = std::panic::catch_unwind(|| Rig::parse(GLTF, &BIN[..cut]))
                .expect("panic on truncated bin");
        }
        for cut in [0, 10, GLTF.len() / 2] {
            let _ = std::panic::catch_unwind(|| Rig::parse(&GLTF[..cut], BIN))
                .expect("panic on truncated gltf");
        }
        let mut g: Value = serde_json::from_slice(GLTF).unwrap();
        g["accessors"][0]["count"] = 1_000_000.into();
        let bad = serde_json::to_vec(&g).unwrap();
        std::panic::catch_unwind(|| Rig::parse(&bad, BIN))
            .expect("panic on huge accessor count")
            .is_err()
            .then_some(())
            .expect("no error");

        // Structural damage must be an error, never a panic
        let corrupt = |edit: &dyn Fn(&mut Value)| {
            let mut g: Value = serde_json::from_slice(GLTF).unwrap();
            edit(&mut g);
            let bad = serde_json::to_vec(&g).unwrap();
            std::panic::catch_unwind(|| Rig::parse(&bad, BIN).is_err()).expect("panic")
        };
        assert!(corrupt(
            &|g| g["nodes"][2]["matrix"] = serde_json::json!([1.0, 2.0, 3.0])
        ));
        // Fewer inverse bind matrices than joints
        assert!(corrupt(&|g| {
            let i = g["skins"][0]["inverseBindMatrices"].as_u64().unwrap() as usize;
            g["accessors"][i]["count"] = 10.into();
        }));
        // Mismatched accessor counts (normals shorter than positions)
        assert!(corrupt(&|g| {
            let i = g["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]
                .as_u64()
                .unwrap() as usize;
            g["accessors"][i]["count"] = 3.into();
        }));
        assert!(corrupt(&|g| g["skins"][0]["joints"][1] = 9999.into()));
        assert!(corrupt(
            &|g| g["nodes"][1]["children"] = serde_json::json!([1])
        ));
        assert!(corrupt(
            &|g| g["bufferViews"][3]["byteOffset"] = u64::MAX.into()
        ));
        assert!(corrupt(
            &|g| g["bufferViews"][3]["byteStride"] = u64::MAX.into()
        ));
        assert!(corrupt(
            &|g| g["meshes"][0]["primitives"][0]["indices"] = 9999.into()
        ));
    }

    // A conf-0 joint is written as position (0,0,0) in the replay: it must neither pull a block's stretch toward the
    // origin nor move the bone end more than the held state allows.
    #[test]
    fn untracked_joint_holds_the_last_stretch() {
        let rep = Replay::parse(SAMPLE).unwrap();
        let r = rig();
        // (zeroed COCO joint, bone whose block ends at the bone below, bone below)
        for (coco, block, end) in [
            (14, 20, 21),
            (16, 21, 22),
            (8, 7, 8),
            (10, 8, 9),
            (13, 25, 26),
            (7, 13, 14),
        ] {
            // First frame after the middle where every mapped limb joint is tracked and nearly still, so the
            // check isolates the zeroing from real motion
            let still = |f: usize| {
                (5..17).all(|c| {
                    let (a, b) = (rep.joints(f - 1, 0)[c], rep.joints(f, 0)[c]);
                    a[3] >= 1.0
                        && b[3] >= 1.0
                        && (V::new(a[0], a[1], a[2]) - V::new(b[0], b[1], b[2])).magnitude() < 0.03
                })
            };
            let f = (rep.frame_count / 2..rep.frame_count)
                .find(|&f| still(f))
                .expect("a still frame");
            let mut player = PlayerRig::new(&r, &Dims::measure(&rep, 0));
            let mut palette = identity_palette();
            for k in 0..f {
                r.pose(rep.joints(k, 0), &mut player, &mut palette);
            }
            let mut j = *rep.joints(f, 0);
            assert!(
                j[coco][3] >= 1.0,
                "pick a frame where COCO {coco} is tracked"
            );
            let mut before = identity_palette();
            let mut p2 = player;
            r.pose(&j, &mut p2, &mut before);
            j[coco] = [0.0; 4];
            let mut after = identity_palette();
            assert!(r.pose(&j, &mut player, &mut after));
            let end_of = |p: &Palette| apply(&p[block], r.rest_t[end]);
            let gap = (end_of(&before) - end_of(&after)).magnitude();
            assert!(gap < 0.05, "COCO {coco}: block end moved {gap} m");
            assert!(
                after.iter().flatten().flatten().all(|x| x.is_finite()),
                "COCO {coco}"
            );
            // Held stretch factors are the previous frame's, never the origin-pointing one
            for k in 0..BONES {
                assert!(
                    (player.stretch[k] - p2.stretch[k]).abs() < 0.3,
                    "bone {k}: {} vs {}",
                    player.stretch[k],
                    p2.stretch[k]
                );
            }
        }
    }
}

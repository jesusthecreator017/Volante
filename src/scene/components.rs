use crate::scene::instance::InstanceRaw;

#[derive(Copy, Clone, Debug)]
pub struct Transform {
    pub position: cgmath::Vector3<f32>,
    pub rotation: cgmath::Quaternion<f32>,
    // ponytail: uniform scale only, so the rotation matrix doubles as the normal matrix.
    // Non-uniform scale needs the inverse-transpose of the model 3x3 instead.
    pub scale: f32,
}

impl Transform {
    pub fn to_raw(self) -> InstanceRaw {
        InstanceRaw {
            model: (cgmath::Matrix4::from_translation(self.position)
                * cgmath::Matrix4::from(self.rotation)
                * cgmath::Matrix4::from_scale(self.scale))
            .into(),
            normal: cgmath::Matrix3::from(self.rotation).into(),
        }
    }
}

// Index into the loaded models list
#[derive(Copy, Clone, Debug)]
pub struct ModelHandle(pub usize);

#[derive(Copy, Clone, Debug)]
pub struct PointLight {
    /// Linear color times intensity (HDR)
    pub color: [f32; 3],
    /// Distance at which the light has faded to 0
    pub range: f32,
}

/// Racquet part (frame or grip) that follows this player's `racquet` track
#[derive(Copy, Clone, Debug)]
pub struct Racquet {
    pub player: usize,
}

/// Calibrated-camera marker cube; hidden (scale 0) unless toggled on with C
#[derive(Copy, Clone, Debug)]
pub struct CameraMarker;

/// The shuttle (one entity) that follows the `shuttle` track
#[derive(Copy, Clone, Debug)]
pub struct Shuttle;

/// One 7-segment bar of the scoreboard: `cell` 0..4 (P1 tens, P1 units, P2 tens, P2 units), `seg` 0..7 (a..g)
#[derive(Copy, Clone, Debug)]
pub struct ScoreSegment {
    pub cell: usize,
    pub seg: usize,
}

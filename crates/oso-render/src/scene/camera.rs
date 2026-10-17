use cgmath::{InnerSpace, SquareMatrix};
use wgpu::util::DeviceExt;

// Orbit limits: pitch is the angle of the eye above the horizontal plane through the target;
// negative = eye below target, i.e. looking up (down to 80 degrees up)
const MIN_PITCH: f32 = -80.0 * std::f32::consts::PI / 180.0;
const MAX_PITCH: f32 = 89.0 * std::f32::consts::PI / 180.0;
pub const MIN_DISTANCE: f32 = 2.0;
pub const MAX_DISTANCE: f32 = 40.0;

// Orbit camera: sits `distance` from `target`, at `yaw` around +Y (0 = on the +X side of the target)
// and `pitch` above the floor, always looking at the target.
pub struct Camera {
    pub target: cgmath::Point3<f32>,
    pub yaw: cgmath::Rad<f32>,
    pub pitch: cgmath::Rad<f32>,
    pub distance: f32,
}

impl Camera {
    pub fn new(
        target: impl Into<cgmath::Point3<f32>>,
        yaw: impl Into<cgmath::Rad<f32>>,
        pitch: impl Into<cgmath::Rad<f32>>,
        distance: f32,
    ) -> Self {
        let mut camera = Self {
            target: target.into(),
            yaw: yaw.into(),
            pitch: pitch.into(),
            distance,
        };
        camera.clamp();
        camera
    }

    // Camera placed at `eye`, looking at `target`. Not clamped: calibrated cameras may sit outside the
    // drag limits; the next drag or scroll clamps as usual.
    pub fn look_from(eye: [f32; 3], target: [f32; 3]) -> Self {
        let d = cgmath::Vector3::from(eye) - cgmath::Vector3::from(target);
        let horizontal = (d.x * d.x + d.z * d.z).sqrt();
        Self {
            target: target.into(),
            yaw: cgmath::Rad(d.z.atan2(d.x)),
            pitch: cgmath::Rad(d.y.atan2(horizontal)),
            distance: d.magnitude(),
        }
    }

    fn looking_at_origin(eye: [f32; 3]) -> Self {
        let mut c = Self::look_from(eye, [0.0; 3]);
        c.clamp();
        c
    }

    // 1 = behind the baseline, 2 = side, 3 = top-down. Anything else leaves the camera alone.
    pub fn apply_preset(&mut self, preset: u8) {
        let eye = match preset {
            1 => [-11.0, 4.0, 0.0],
            2 => [0.0, 5.0, 12.0],
            // Straight above would make yaw meaningless; the pitch clamp stops at 89 degrees.
            // Just under the 11.5 m ceiling. Yaw looks toward -Z so +X (the court length) runs across a wide screen.
            3 => [0.0, 11.0, 0.01],
            _ => return,
        };
        let mut c = Self::looking_at_origin(eye);
        if preset == 3 {
            c.pitch = cgmath::Rad(MAX_PITCH);
            c.distance = 11.0 / MAX_PITCH.sin();
        }
        *self = c;
    }

    pub fn eye(&self) -> cgmath::Point3<f32> {
        let (sin_pitch, cos_pitch) = self.pitch.0.sin_cos();
        let (sin_yaw, cos_yaw) = self.yaw.0.sin_cos();
        self.target
            + cgmath::Vector3::new(cos_pitch * cos_yaw, sin_pitch, cos_pitch * sin_yaw)
                * self.distance
    }

    pub fn view_matrix(&self) -> cgmath::Matrix4<f32> {
        cgmath::Matrix4::look_at_rh(self.eye(), self.target, cgmath::Vector3::unit_y())
    }

    pub fn clamp(&mut self) {
        self.pitch.0 = self.pitch.0.clamp(MIN_PITCH, MAX_PITCH);
        self.distance = self.distance.clamp(MIN_DISTANCE, MAX_DISTANCE);
    }
}

pub const OPENGL_TO_WGPU_MATRIX: cgmath::Matrix4<f32> = cgmath::Matrix4::from_cols(
    cgmath::Vector4::new(1.0, 0.0, 0.0, 0.0),
    cgmath::Vector4::new(0.0, 1.0, 0.0, 0.0),
    cgmath::Vector4::new(0.0, 0.0, 0.5, 0.0),
    cgmath::Vector4::new(0.0, 0.0, 0.5, 1.0),
);

// The lens: kept apart from Camera because only a window resize changes it
pub struct Projection {
    aspect: f32,
    fovy: cgmath::Rad<f32>,
    znear: f32,
    zfar: f32,
}

impl Projection {
    pub fn new(aspect: f32, fovy: impl Into<cgmath::Rad<f32>>, znear: f32, zfar: f32) -> Self {
        Self {
            aspect,
            fovy: fovy.into(),
            znear,
            zfar,
        }
    }

    pub fn resize(&mut self, aspect: f32) {
        self.aspect = aspect;
    }

    pub fn set_fovy(&mut self, fovy: impl Into<cgmath::Rad<f32>>) {
        self.fovy = fovy.into();
    }

    pub fn matrix(&self) -> cgmath::Matrix4<f32> {
        OPENGL_TO_WGPU_MATRIX * cgmath::perspective(self.fovy, self.aspect, self.znear, self.zfar)
    }
}

#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
struct CameraUniform {
    // vec4 because uniforms need 16-byte alignment
    view_position: [f32; 4],
    view_proj: [[f32; 4]; 4],
    // Appended after view_proj so shaders that only declare the first two fields still line up.
    // Unused since the sky was removed (Phase 8); kept so the uniform layout stays stable.
    inv_proj: [[f32; 4]; 4],
    inv_view: [[f32; 4]; 4],
}

impl CameraUniform {
    fn new(camera: &Camera, projection: &Projection) -> Self {
        let proj = projection.matrix();
        let view = camera.view_matrix();
        Self {
            view_position: camera.eye().to_homogeneous().into(),
            view_proj: (proj * view).into(),
            // Perspective and look-to matrices are always invertible for a valid camera
            inv_proj: proj.invert().unwrap_or(cgmath::Matrix4::identity()).into(),
            inv_view: view.invert().unwrap_or(cgmath::Matrix4::identity()).into(),
        }
    }
}

// GPU side of the camera: the uniform buffer and its bind group
pub struct CameraBinding {
    buffer: wgpu::Buffer,
    pub layout: wgpu::BindGroupLayout,
    pub bind_group: wgpu::BindGroup,
}

impl CameraBinding {
    pub fn new(device: &wgpu::Device, camera: &Camera, projection: &Projection) -> Self {
        let buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Camera Buffer"),
            contents: bytemuck::cast_slice(&[CameraUniform::new(camera, projection)]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });

        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("camera_bind_group_layout"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX | wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });

        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("camera_bind_group"),
            layout: &layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: buffer.as_entire_binding(),
            }],
        });

        Self {
            buffer,
            layout,
            bind_group,
        }
    }

    pub fn update(&self, queue: &wgpu::Queue, camera: &Camera, projection: &Projection) {
        queue.write_buffer(
            &self.buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform::new(camera, projection)]),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn top_down_preset_is_above_target_at_distance() {
        let mut camera = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        camera.apply_preset(3);
        let eye = camera.eye();
        assert!(eye.y > 10.0, "{eye:?}");
        let dist = (eye - camera.target).magnitude();
        assert!((dist - camera.distance).abs() < 1e-3, "{dist}");
    }

    #[test]
    fn baseline_preset_matches_spec_position() {
        let mut camera = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        camera.apply_preset(1);
        let eye = camera.eye();
        assert!((eye.x + 11.0).abs() < 1e-3 && (eye.y - 4.0).abs() < 1e-3 && eye.z.abs() < 1e-3);
        camera.apply_preset(2);
        let eye = camera.eye();
        assert!(eye.x.abs() < 1e-3 && (eye.y - 5.0).abs() < 1e-3 && (eye.z - 12.0).abs() < 1e-3);
    }

    #[test]
    fn look_from_reproduces_eye_and_target() {
        let c = Camera::look_from([-10.5, 3.5, 2.0], [1.0, 0.0, -1.0]);
        let e = c.eye();
        assert!((e.x + 10.5).abs() < 1e-4 && (e.y - 3.5).abs() < 1e-4 && (e.z - 2.0).abs() < 1e-4);
    }

    #[test]
    fn clamp_holds() {
        let mut camera = Camera::new(
            (0.0, 0.0, 0.0),
            cgmath::Deg(0.0),
            cgmath::Deg(200.0),
            1000.0,
        );
        assert!(camera.pitch.0 <= MAX_PITCH && camera.distance == MAX_DISTANCE);
        camera.pitch.0 = -1.0;
        camera.distance = 0.0;
        camera.clamp();
        assert!(camera.pitch.0 >= MIN_PITCH && camera.distance == MIN_DISTANCE);
    }
}

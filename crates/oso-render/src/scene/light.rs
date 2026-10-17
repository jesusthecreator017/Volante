use wgpu::util::DeviceExt;

pub const MAX_LIGHTS: usize = 8;

// Keep in sync with Light / Lights in shaders/lighting.wgsl (std140: vec3 + f32 packs into 16 B)
#[repr(C)]
#[derive(Copy, Clone, Debug, Default, bytemuck::Pod, bytemuck::Zeroable)]
pub struct Light {
    pub position: [f32; 3],
    /// Distance at which the light has faded to exactly 0
    pub range: f32,
    /// Linear color times intensity (HDR, can exceed 1)
    pub color: [f32; 3],
    pub _padding: f32,
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Default, bytemuck::Pod, bytemuck::Zeroable)]
pub struct LightUniform {
    pub count: u32,
    pub _padding: [u32; 3],
    pub lights: [Light; MAX_LIGHTS],
}

impl LightUniform {
    // Takes the first MAX_LIGHTS; extra lights are dropped
    pub fn from_lights(lights: impl IntoIterator<Item = Light>) -> Self {
        let mut u = Self::default();
        for l in lights.into_iter().take(MAX_LIGHTS) {
            u.lights[u.count as usize] = l;
            u.count += 1;
        }
        u
    }
}

// CPU mirror of `attenuation` in shaders/lighting.wgsl: inverse square (full strength inside 1 m) times a smooth
// window that reaches exactly 0 at `range`, so a light never pops at its boundary.
pub fn attenuation(distance: f32, range: f32) -> f32 {
    let window = (1.0 - (distance / range).powi(4)).clamp(0.0, 1.0);
    window * window / (distance * distance).max(1.0)
}

pub struct LightBinding {
    pub buffer: wgpu::Buffer,
    pub layout: wgpu::BindGroupLayout,
    pub bind_group: wgpu::BindGroup,
}

impl LightBinding {
    pub fn new(device: &wgpu::Device, light_uniform: LightUniform) -> Self {
        let buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Light Buffer"),
            contents: bytemuck::cast_slice(&[light_uniform]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });

        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("Light Bind Group Layout"),
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
            layout: &layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: buffer.as_entire_binding(),
            }],
            label: Some("Light Bind Group"),
        });

        Self {
            buffer,
            layout,
            bind_group,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uniform_matches_wgsl_layout() {
        // Light: vec3 + f32, vec3 + f32 = 32 B; Lights: u32 + 12 B pad, then array<Light, 8> at offset 16
        assert_eq!(size_of::<Light>(), 32);
        assert_eq!(std::mem::offset_of!(Light, color), 16);
        assert_eq!(std::mem::offset_of!(LightUniform, lights), 16);
        assert_eq!(size_of::<LightUniform>(), 16 + 32 * MAX_LIGHTS);
        assert_eq!(size_of::<LightUniform>() % 16, 0);
    }

    #[test]
    fn attenuation_is_full_near_and_zero_at_range() {
        assert!((attenuation(1.0, 25.0) - 1.0).abs() < 1e-3);
        assert!((attenuation(0.2, 25.0) - attenuation(1.0, 25.0)).abs() < 1e-3);
        assert_eq!(attenuation(25.0, 25.0), 0.0);
        assert_eq!(attenuation(40.0, 25.0), 0.0);
        assert!(attenuation(10.0, 25.0) > attenuation(20.0, 25.0));
    }

    #[test]
    fn from_lights_caps_at_max() {
        let l = Light {
            range: 25.0,
            ..Default::default()
        };
        let u = LightUniform::from_lights(vec![l; MAX_LIGHTS + 3]);
        assert_eq!(u.count as usize, MAX_LIGHTS);
        assert_eq!(LightUniform::from_lights([]).count, 0);
    }
}

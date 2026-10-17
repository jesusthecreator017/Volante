//! Skinned humanoid pass: one shared mesh, a uniform buffer + bind group (color and joint palette) per player.

use wgpu::util::DeviceExt;

use crate::renderer::hdr::HDR_FORMAT;
use crate::renderer::pipeline::{create_render_pipeline, depth_state};
use crate::scene::camera::CameraBinding;
use crate::scene::light::LightBinding;
use crate::scene::rig::{Palette, Rig, SkinnedVertex};

// Keep in sync with Player in shaders/skinned.wgsl (std140: vec4 then 32 mat4)
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct PlayerUniform {
    pub color: [f32; 4],
    pub joints: Palette,
}

impl SkinnedVertex {
    const ATTRIBS: [wgpu::VertexAttribute; 4] =
        wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3, 2 => Float32x4, 3 => Float32x4];

    fn desc() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: size_of::<SkinnedVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRIBS,
        }
    }
}

struct PlayerSlot {
    buffer: wgpu::Buffer,
    bind_group: wgpu::BindGroup,
    uniform: PlayerUniform,
    visible: bool,
}

pub struct SkinnedPass {
    pipeline: wgpu::RenderPipeline,
    vertices: wgpu::Buffer,
    indices: wgpu::Buffer,
    index_count: u32,
    players: Vec<PlayerSlot>,
}

impl SkinnedPass {
    pub fn new(
        device: &wgpu::Device,
        rig: &Rig,
        colors: &[[f32; 3]],
        camera: &CameraBinding,
        light: &LightBinding,
        sample_count: u32,
    ) -> Self {
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("skinned_player_layout"),
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
        let pipeline = create_render_pipeline(
            device,
            HDR_FORMAT,
            &[Some(&layout), Some(&camera.layout), Some(&light.layout)],
            &[Some(SkinnedVertex::desc())],
            depth_state(true),
            wgpu::PrimitiveTopology::TriangleList,
            wgpu::ShaderModuleDescriptor {
                label: Some("Skinned Shader"),
                source: wgpu::ShaderSource::Wgsl(
                    concat!(
                        include_str!("../shaders/lighting.wgsl"),
                        include_str!("../shaders/skinned.wgsl")
                    )
                    .into(),
                ),
            },
            sample_count,
        );
        let vertices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Skinned vertices"),
            contents: bytemuck::cast_slice(&rig.vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let indices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Skinned indices"),
            contents: bytemuck::cast_slice(&rig.indices),
            usage: wgpu::BufferUsages::INDEX,
        });
        let players = colors
            .iter()
            .map(|c| {
                let uniform = PlayerUniform {
                    color: [c[0], c[1], c[2], 1.0],
                    joints: crate::scene::rig::identity_palette(),
                };
                let buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("Skinned player uniform"),
                    contents: bytemuck::bytes_of(&uniform),
                    usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                });
                let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
                    label: Some("skinned_player_bind_group"),
                    layout: &layout,
                    entries: &[wgpu::BindGroupEntry {
                        binding: 0,
                        resource: buffer.as_entire_binding(),
                    }],
                });
                PlayerSlot {
                    buffer,
                    bind_group,
                    uniform,
                    visible: false,
                }
            })
            .collect();
        Self {
            pipeline,
            vertices,
            indices,
            index_count: rig.indices.len() as u32,
            players,
        }
    }

    // Color of each player's body, written to the GPU with the next `upload`
    pub fn set_colors(&mut self, colors: &[[f32; 3]]) {
        for (slot, c) in self.players.iter_mut().zip(colors) {
            slot.uniform.color = [c[0], c[1], c[2], 1.0];
        }
    }

    // Writes each player's palette (None hides the player)
    pub fn upload(&mut self, queue: &wgpu::Queue, palettes: &[Option<Palette>]) {
        for (slot, palette) in self.players.iter_mut().zip(palettes) {
            slot.visible = palette.is_some();
            if let Some(p) = palette {
                slot.uniform.joints = *p;
                queue.write_buffer(&slot.buffer, 0, bytemuck::bytes_of(&slot.uniform));
            }
        }
    }

    pub fn draw(
        &self,
        pass: &mut wgpu::RenderPass<'_>,
        camera: &wgpu::BindGroup,
        light: &wgpu::BindGroup,
    ) {
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(1, camera, &[]);
        pass.set_bind_group(2, light, &[]);
        pass.set_vertex_buffer(0, self.vertices.slice(..));
        pass.set_index_buffer(self.indices.slice(..), wgpu::IndexFormat::Uint32);
        for slot in self.players.iter().filter(|s| s.visible) {
            pass.set_bind_group(0, &slot.bind_group, &[]);
            pass.draw_indexed(0..self.index_count, 0, 0..1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scene::rig::MAX_JOINTS;

    #[test]
    fn uniform_matches_wgsl_layout() {
        // vec4 color (16 B) then array<mat4x4, 32> (64 B stride), 16 B aligned; well under WebGL2's 16 KB block limit
        assert_eq!(size_of::<PlayerUniform>(), MAX_JOINTS * 64 + 16);
        assert_eq!(size_of::<PlayerUniform>() % 16, 0);
        assert_eq!(std::mem::offset_of!(PlayerUniform, joints), 16);
        assert_eq!(size_of::<SkinnedVertex>(), 56);
    }
}

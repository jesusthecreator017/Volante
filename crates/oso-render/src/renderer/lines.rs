//! Generic debug line batch (LineList): skeletons now, shuttle trail later.
//! Vertices are in court meters, drawn with the scene camera into the HDR target.

use crate::renderer::hdr::HDR_FORMAT;
use crate::renderer::pipeline::{create_render_pipeline, depth_state};
use crate::renderer::upload_vertices;
use crate::scene::camera::CameraBinding;

// Keep in sync with VertexInput in shaders/lines.wgsl
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct LineVertex {
    pub position: [f32; 3],
    pub color: [f32; 3],
}

impl LineVertex {
    const ATTRIBS: [wgpu::VertexAttribute; 2] =
        wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3];

    fn desc() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: size_of::<LineVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRIBS,
        }
    }
}

pub struct LinePass {
    pipeline: wgpu::RenderPipeline,
    buffer: Option<wgpu::Buffer>,
    count: u32,
}

impl LinePass {
    pub fn new(device: &wgpu::Device, camera: &CameraBinding, sample_count: u32) -> Self {
        let pipeline = create_render_pipeline(
            device,
            HDR_FORMAT,
            &[Some(&camera.layout)],
            &[Some(LineVertex::desc())],
            depth_state(true),
            wgpu::PrimitiveTopology::LineList,
            wgpu::ShaderModuleDescriptor {
                label: Some("Line Shader"),
                source: wgpu::ShaderSource::Wgsl(include_str!("../shaders/lines.wgsl").into()),
            },
            sample_count,
        );
        Self {
            pipeline,
            buffer: None,
            count: 0,
        }
    }

    // Replaces this frame's lines (pairs of vertices)
    pub fn upload(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, vertices: &[LineVertex]) {
        upload_vertices(device, queue, &mut self.buffer, vertices);
        self.count = vertices.len() as u32;
    }

    pub fn draw(&self, pass: &mut wgpu::RenderPass<'_>, camera: &wgpu::BindGroup) {
        let Some(buffer) = self.buffer.as_ref().filter(|_| self.count > 0) else {
            return;
        };
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, camera, &[]);
        pass.set_vertex_buffer(0, buffer.slice(..));
        pass.draw(0..self.count, 0..1);
    }
}

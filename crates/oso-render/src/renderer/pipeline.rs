use crate::renderer::texture::Texture;

#[allow(clippy::too_many_arguments)]
pub fn create_render_pipeline(
    device: &wgpu::Device,
    format: wgpu::TextureFormat,
    bind_group_layouts: &[Option<&wgpu::BindGroupLayout>],
    vertex_layouts: &[Option<wgpu::VertexBufferLayout>],
    depth_stencil: Option<wgpu::DepthStencilState>,
    topology: wgpu::PrimitiveTopology,
    shader: wgpu::ShaderModuleDescriptor,
    sample_count: u32,
) -> wgpu::RenderPipeline {
    let shader = device.create_shader_module(shader);

    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("Render Pipeline Layout"),
        bind_group_layouts,
        immediate_size: 0,
    });

    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("Render Pipeline"),
        layout: Some(&layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs_main"),
            buffers: vertex_layouts,
            compilation_options: wgpu::PipelineCompilationOptions::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: Some("fs_main"),
            targets: &[Some(wgpu::ColorTargetState {
                format,
                blend: Some(wgpu::BlendState::REPLACE),
                write_mask: wgpu::ColorWrites::ALL,
            })],
            compilation_options: wgpu::PipelineCompilationOptions::default(),
        }),
        primitive: wgpu::PrimitiveState {
            topology,
            strip_index_format: None,
            front_face: wgpu::FrontFace::Ccw,
            cull_mode: Some(wgpu::Face::Back),
            polygon_mode: wgpu::PolygonMode::Fill,
            unclipped_depth: false,
            conservative: false,
        },
        depth_stencil,
        multisample: wgpu::MultisampleState {
            count: sample_count,
            mask: !0,
            alpha_to_coverage_enabled: false,
        },
        multiview_mask: None,
        cache: None,
    })
}

// LessEqual so coplanar redraws (e.g. identical overlapping court mats) pass instead of flickering
pub fn depth_state(write: bool) -> Option<wgpu::DepthStencilState> {
    Some(wgpu::DepthStencilState {
        format: Texture::DEPTH_FORMAT,
        depth_write_enabled: Some(write),
        depth_compare: Some(wgpu::CompareFunction::LessEqual),
        stencil: wgpu::StencilState::default(),
        bias: wgpu::DepthBiasState::default(),
    })
}

use crate::renderer::pipeline::create_render_pipeline;

// Scene renders into this, then tonemapping squeezes it into the surface's 0..1 range
pub const HDR_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float;

// Offscreen HDR color target plus the fullscreen pass that tonemaps it onto the surface
pub struct HdrPipeline {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    view: wgpu::TextureView,
    // Multisampled scene color when MSAA is on; it resolves into `view`
    msaa_view: Option<wgpu::TextureView>,
    bind_group: wgpu::BindGroup,
}

impl HdrPipeline {
    pub fn new(
        device: &wgpu::Device,
        config: &wgpu::SurfaceConfiguration,
        sample_count: u32,
    ) -> Self {
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("hdr_bind_group_layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        multisampled: false,
                        view_dimension: wgpu::TextureViewDimension::D2,
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });

        // Target and surface are the same size, so no filtering needed
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("HDR Sampler"),
            ..Default::default()
        });

        let pipeline = create_render_pipeline(
            device,
            config.format,
            &[Some(&layout)],
            &[],
            None,
            wgpu::PrimitiveTopology::TriangleList,
            wgpu::ShaderModuleDescriptor {
                label: Some("HDR Shader"),
                source: wgpu::ShaderSource::Wgsl(include_str!("../shaders/hdr.wgsl").into()),
            },
            1,
        );

        let (view, bind_group) =
            create_target(device, &layout, &sampler, config.width, config.height);
        let msaa_view = create_msaa(device, config.width, config.height, sample_count);

        Self {
            msaa_view,
            pipeline,
            layout,
            sampler,
            view,
            bind_group,
        }
    }

    pub fn resize(&mut self, device: &wgpu::Device, width: u32, height: u32, sample_count: u32) {
        (self.view, self.bind_group) =
            create_target(device, &self.layout, &self.sampler, width, height);
        self.msaa_view = create_msaa(device, width, height, sample_count);
    }

    // What the scene pass renders into: (color view, resolve target). Without MSAA it draws straight into the target.
    pub fn scene_target(&self) -> (&wgpu::TextureView, Option<&wgpu::TextureView>) {
        match &self.msaa_view {
            Some(msaa) => (msaa, Some(&self.view)),
            None => (&self.view, None),
        }
    }

    // Tonemap the HDR target onto the surface
    pub fn process(&self, encoder: &mut wgpu::CommandEncoder, output: &wgpu::TextureView) {
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("HDR::process"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: output,
                resolve_target: None,
                depth_slice: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Load,
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            occlusion_query_set: None,
            timestamp_writes: None,
            multiview_mask: None,
        });
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, &self.bind_group, &[]);
        // One triangle big enough to cover the screen, vertices made up in the shader
        pass.draw(0..3, 0..1);
    }
}

fn create_target(
    device: &wgpu::Device,
    layout: &wgpu::BindGroupLayout,
    sampler: &wgpu::Sampler,
    width: u32,
    height: u32,
) -> (wgpu::TextureView, wgpu::BindGroup) {
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("HDR Target"),
        size: wgpu::Extent3d {
            // Zero-size textures are a validation error
            width: width.max(1),
            height: height.max(1),
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: HDR_FORMAT,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::RENDER_ATTACHMENT,
        view_formats: &[],
    });
    let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
    let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("hdr_bind_group"),
        layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: wgpu::BindingResource::TextureView(&view),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: wgpu::BindingResource::Sampler(sampler),
            },
        ],
    });
    (view, bind_group)
}

fn create_msaa(
    device: &wgpu::Device,
    width: u32,
    height: u32,
    sample_count: u32,
) -> Option<wgpu::TextureView> {
    (sample_count > 1).then(|| {
        device
            .create_texture(&wgpu::TextureDescriptor {
                label: Some("HDR MSAA Target"),
                size: wgpu::Extent3d {
                    width: width.max(1),
                    height: height.max(1),
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count,
                dimension: wgpu::TextureDimension::D2,
                format: HDR_FORMAT,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
                view_formats: &[],
            })
            .create_view(&wgpu::TextureViewDescriptor::default())
    })
}

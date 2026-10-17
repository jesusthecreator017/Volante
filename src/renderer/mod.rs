//! GPU-side code: device/surface, pipelines, textures and meshes.
//! `Renderer` reads the ECS world each frame and draws whatever it finds.

pub mod gpu;
pub mod hdr;
pub mod lines;
pub mod model;
pub mod pipeline;
pub mod skinned;
pub mod texture;

use bytemuck::Zeroable;

use crate::renderer::gpu::Gpu;
use crate::renderer::hdr::{HDR_FORMAT, HdrPipeline};
use crate::renderer::lines::{LinePass, LineVertex};
use crate::renderer::model::{Material, Model, ModelVertex, Vertex};
use crate::renderer::pipeline::{create_render_pipeline, depth_state};
use crate::renderer::skinned::SkinnedPass;
use crate::renderer::texture::Texture;
use crate::scene::camera::{Camera, CameraBinding, Projection};
use crate::scene::components::{ModelHandle, PointLight, Transform};
use crate::scene::instance::InstanceRaw;
use crate::scene::light::{Light, LightBinding, LightUniform};
use crate::scene::rig::{Palette, Rig};

pub struct Renderer {
    pub gpu: Gpu,
    pub material_layout: wgpu::BindGroupLayout,
    render_pipeline: wgpu::RenderPipeline,
    line_pass: LinePass,
    // None until `set_rig`, or when the rig failed to load
    skinned: Option<SkinnedPass>,
    hdr: HdrPipeline,
    camera_binding: CameraBinding,
    light_binding: LightBinding,
    depth_texture: Texture,
    // One per model, grown on demand. None until that model has instances.
    instance_buffers: Vec<Option<wgpu::Buffer>>,
}

impl Renderer {
    pub fn new(gpu: Gpu, camera: &Camera, projection: &Projection) -> anyhow::Result<Self> {
        let device = &gpu.device;

        let material_layout = Material::bind_group_layout(device);
        let camera_binding = CameraBinding::new(device, camera, projection);
        let light_binding = LightBinding::new(device, LightUniform::zeroed());
        let depth_texture =
            Texture::create_depth_texture(device, &gpu.config, "Depth Texture", gpu.sample_count)?;
        let hdr = HdrPipeline::new(device, &gpu.config, gpu.sample_count);

        // Scene pipelines all draw into the HDR target, not the surface
        let render_pipeline = create_render_pipeline(
            device,
            HDR_FORMAT,
            &[
                Some(&material_layout),
                Some(&camera_binding.layout),
                Some(&light_binding.layout),
            ],
            &[Some(ModelVertex::desc()), Some(InstanceRaw::desc())],
            depth_state(true),
            wgpu::PrimitiveTopology::TriangleList,
            wgpu::ShaderModuleDescriptor {
                label: Some("Model Shader"),
                source: wgpu::ShaderSource::Wgsl(
                    concat!(
                        include_str!("../shaders/lighting.wgsl"),
                        include_str!("../shaders/model.wgsl")
                    )
                    .into(),
                ),
            },
            gpu.sample_count,
        );

        let line_pass = LinePass::new(device, &camera_binding, gpu.sample_count);

        Ok(Self {
            gpu,
            material_layout,
            render_pipeline,
            line_pass,
            skinned: None,
            hdr,
            camera_binding,
            light_binding,
            depth_texture,
            instance_buffers: Vec::new(),
        })
    }

    /// Enables the skinned humanoid pass: one palette slot per color.
    pub fn set_rig(&mut self, rig: &Rig, colors: &[[f32; 3]]) {
        self.skinned = Some(SkinnedPass::new(
            &self.gpu.device,
            rig,
            colors,
            &self.camera_binding,
            &self.light_binding,
            self.gpu.sample_count,
        ));
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        self.gpu.resize(width, height);
        // Depth texture must match the new surface size
        match Texture::create_depth_texture(
            &self.gpu.device,
            &self.gpu.config,
            "Depth Texture",
            self.gpu.sample_count,
        ) {
            Ok(texture) => self.depth_texture = texture,
            Err(e) => log::error!("{e}"),
        }
        self.hdr.resize(
            &self.gpu.device,
            self.gpu.config.width,
            self.gpu.config.height,
            self.gpu.sample_count,
        );
    }

    pub fn render(
        &mut self,
        world: &hecs::World,
        models: &[Model],
        camera: &Camera,
        projection: &Projection,
        lines: &[LineVertex],
        // One per player; None hides that player
        palettes: &[Option<Palette>],
    ) -> anyhow::Result<()> {
        // We cant render unless the surface is configured
        if !self.gpu.is_surface_configured() {
            return Ok(());
        }

        let Some(output) = self.gpu.next_frame()? else {
            return Ok(());
        };
        // Create the view, it will allow us to manipulate the display
        let view = output
            .texture
            .create_view(&wgpu::TextureViewDescriptor::default());
        let encoder = self.encode(world, models, camera, projection, lines, palettes, &view);
        self.gpu.queue.submit(std::iter::once(encoder.finish()));
        self.gpu.queue.present(output);

        Ok(())
    }

    // Records the whole frame (extract, scene pass, tonemap) into `target`, which must be `gpu.config.format`
    #[allow(clippy::too_many_arguments)]
    fn encode(
        &mut self,
        world: &hecs::World,
        models: &[Model],
        camera: &Camera,
        projection: &Projection,
        lines: &[LineVertex],
        palettes: &[Option<Palette>],
        target: &wgpu::TextureView,
    ) -> wgpu::CommandEncoder {
        // Extract: copy what the world says into GPU buffers
        self.camera_binding
            .update(&self.gpu.queue, camera, projection);

        // Indoor lights: up to MAX_LIGHTS point lights (the rest are ignored), rewritten every frame (272 bytes)
        let lights =
            LightUniform::from_lights(world.query::<(&Transform, &PointLight)>().iter().map(
                |(t, l)| Light {
                    position: t.position.into(),
                    range: l.range,
                    color: l.color,
                    _padding: 0.0,
                },
            ));
        self.gpu
            .queue
            .write_buffer(&self.light_binding.buffer, 0, bytemuck::bytes_of(&lights));

        let batches = extract_instances(world, models.len());
        self.instance_buffers.resize_with(models.len(), || None);
        for (slot, batch) in self.instance_buffers.iter_mut().zip(&batches) {
            upload_vertices(&self.gpu.device, &self.gpu.queue, slot, batch);
        }
        self.line_pass
            .upload(&self.gpu.device, &self.gpu.queue, lines);
        if let Some(skinned) = &mut self.skinned {
            skinned.upload(&self.gpu.queue, palettes);
        }

        // The encoder will be a middleman translating our commands to something the gpu can
        // understand
        let mut encoder = self
            .gpu
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("Render Encoder"),
            });

        {
            let (scene_view, resolve_target) = self.hdr.scene_target();
            let mut render_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("Render Pass"),
                color_attachments: &[
                    // This is where location(0) of the fragment shader lives.
                    // The scene goes into the HDR target; walls enclose everything so the clear color never shows.
                    Some(wgpu::RenderPassColorAttachment {
                        view: scene_view,
                        resolve_target,
                        depth_slice: None,
                        ops: wgpu::Operations {
                            load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                            // The multisampled copy is only needed to resolve from
                            store: if resolve_target.is_some() {
                                wgpu::StoreOp::Discard
                            } else {
                                wgpu::StoreOp::Store
                            },
                        },
                    }),
                ],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.depth_texture.view,
                    depth_ops: Some(wgpu::Operations {
                        load: wgpu::LoadOp::Clear(1.0),
                        store: wgpu::StoreOp::Store,
                    }),
                    stencil_ops: None,
                }),
                occlusion_query_set: None,
                timestamp_writes: None,
                multiview_mask: None,
            });

            render_pass.set_pipeline(&self.render_pipeline);
            render_pass.set_bind_group(1, &self.camera_binding.bind_group, &[]);
            render_pass.set_bind_group(2, &self.light_binding.bind_group, &[]);
            for ((model, batch), buffer) in models.iter().zip(&batches).zip(&self.instance_buffers)
            {
                let Some(buffer) = buffer.as_ref().filter(|_| !batch.is_empty()) else {
                    continue;
                };
                render_pass.set_vertex_buffer(1, buffer.slice(..));
                for mesh in &model.meshes {
                    let material = &model.materials[mesh.material];
                    render_pass.set_bind_group(0, &material.bind_group, &[]);
                    render_pass.set_vertex_buffer(0, mesh.vertex_buffer.slice(..));
                    render_pass
                        .set_index_buffer(mesh.index_buffer.slice(..), wgpu::IndexFormat::Uint32);
                    render_pass.draw_indexed(0..mesh.num_elements, 0, 0..batch.len() as u32);
                }
            }

            if let Some(skinned) = &self.skinned {
                skinned.draw(
                    &mut render_pass,
                    &self.camera_binding.bind_group,
                    &self.light_binding.bind_group,
                );
            }

            self.line_pass
                .draw(&mut render_pass, &self.camera_binding.bind_group);
        }

        // HDR target -> tonemapped -> surface
        self.hdr.process(&mut encoder, target);

        encoder
    }

    /// Renders one frame offscreen at the current surface size and returns tightly packed RGBA8 (sRGB) pixels.
    #[cfg(not(target_arch = "wasm32"))]
    pub fn render_to_rgba(
        &mut self,
        world: &hecs::World,
        models: &[Model],
        camera: &Camera,
        projection: &Projection,
        lines: &[LineVertex],
        palettes: &[Option<Palette>],
    ) -> anyhow::Result<Vec<u8>> {
        let (width, height) = (self.gpu.config.width, self.gpu.config.height);
        let format = self.gpu.config.format;
        let texture = self.gpu.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("Screenshot Target"),
            size: wgpu::Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let mut encoder = self.encode(world, models, camera, projection, lines, palettes, &view);

        let (padded, _) = padded_row_bytes(width);
        let buffer = self.gpu.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("Screenshot Readback"),
            size: padded as u64 * height as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        encoder.copy_texture_to_buffer(
            texture.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &buffer,
                layout: wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(padded),
                    rows_per_image: Some(height),
                },
            },
            wgpu::Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
        );
        self.gpu.queue.submit(std::iter::once(encoder.finish()));

        let slice = buffer.slice(..);
        slice.map_async(wgpu::MapMode::Read, |r| {
            if let Err(e) = r {
                log::error!("screenshot map failed: {e}");
            }
        });
        self.gpu.device.poll(wgpu::PollType::wait_indefinitely())?;
        let data = slice.get_mapped_range()?;
        let bgra = matches!(
            format,
            wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb
        );
        Ok(unpad_rows(&data, width, height, bgra))
    }
}

// wgpu wants each copied row padded to 256 bytes. Returns (padded bytes per row, unpadded bytes per row).
#[cfg(not(target_arch = "wasm32"))]
fn padded_row_bytes(width: u32) -> (u32, u32) {
    let unpadded = width * 4;
    let align = wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
    (unpadded.div_ceil(align) * align, unpadded)
}

// Strips the row padding; swaps B and R when the texture was BGRA
#[cfg(not(target_arch = "wasm32"))]
fn unpad_rows(data: &[u8], width: u32, height: u32, bgra: bool) -> Vec<u8> {
    let (padded, unpadded) = padded_row_bytes(width);
    let mut out = Vec::with_capacity(unpadded as usize * height as usize);
    for row in data.chunks(padded as usize).take(height as usize) {
        out.extend_from_slice(&row[..unpadded as usize]);
    }
    if bgra {
        out.as_chunks_mut::<4>()
            .0
            .iter_mut()
            .for_each(|p| p.swap(0, 2));
    }
    out
}

// Groups every drawable entity's transform by the model it uses
fn extract_instances(world: &hecs::World, model_count: usize) -> Vec<Vec<InstanceRaw>> {
    let mut batches = vec![Vec::new(); model_count];
    for (transform, handle) in world.query::<(&Transform, &ModelHandle)>().iter() {
        batches[handle.0].push(transform.to_raw());
    }
    batches
}

// Writes the batch into the slot's buffer, reallocating (to the next power of two) only when it outgrows it
fn upload_vertices<T: bytemuck::Pod>(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    slot: &mut Option<wgpu::Buffer>,
    batch: &[T],
) {
    if batch.is_empty() {
        return;
    }
    let bytes: &[u8] = bytemuck::cast_slice(batch);
    if slot.as_ref().is_none_or(|b| b.size() < bytes.len() as u64) {
        *slot = Some(device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("Vertex Buffer"),
            size: (bytes.len() as u64).next_power_of_two(),
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        }));
    }
    if let Some(buffer) = slot {
        queue.write_buffer(buffer, 0, bytes);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::Rotation3;

    #[test]
    fn extract_groups_by_model() {
        let mut world = hecs::World::new();
        let transform = Transform {
            position: (0.0, 0.0, 0.0).into(),
            rotation: cgmath::Quaternion::from_angle_y(cgmath::Deg(0.0)),
            scale: 1.0,
        };
        for _ in 0..3 {
            world.spawn((transform, ModelHandle(0)));
        }
        world.spawn((transform, ModelHandle(1)));
        // No ModelHandle, so not drawn
        world.spawn((
            transform,
            PointLight {
                color: [1.0; 3],
                range: 25.0,
            },
        ));

        let batches = extract_instances(&world, 2);
        assert_eq!(batches[0].len(), 3);
        assert_eq!(batches[1].len(), 1);
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn row_padding_is_256_aligned_and_stripped() {
        assert_eq!(padded_row_bytes(1600), (6400, 6400));
        assert_eq!(padded_row_bytes(100), (512, 400));
        assert_eq!(padded_row_bytes(1), (256, 4));
        // 2x2 image, rows padded to 256 bytes, BGRA -> RGBA
        let mut data = vec![0u8; 512];
        data[..8].copy_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
        data[256..264].copy_from_slice(&[9, 10, 11, 12, 13, 14, 15, 16]);
        assert_eq!(
            unpad_rows(&data, 2, 2, false),
            (1..=16).collect::<Vec<u8>>()
        );
        assert_eq!(unpad_rows(&data, 2, 2, true)[..4], [3, 2, 1, 4]);
    }
}

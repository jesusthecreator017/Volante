use wgpu::util::DeviceExt;

use crate::renderer::texture::Texture;

pub trait Vertex {
    fn desc() -> wgpu::VertexBufferLayout<'static>;
}

#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct ModelVertex {
    pub position: [f32; 3],
    pub tex_coords: [f32; 2],
    pub normal: [f32; 3],
    pub tangent: [f32; 3],
    pub bitangent: [f32; 3],
}

impl Vertex for ModelVertex {
    fn desc() -> wgpu::VertexBufferLayout<'static> {
        use std::mem;
        wgpu::VertexBufferLayout {
            array_stride: mem::size_of::<ModelVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &[
                wgpu::VertexAttribute {
                    offset: 0,
                    shader_location: 0,
                    format: wgpu::VertexFormat::Float32x3,
                },
                wgpu::VertexAttribute {
                    offset: mem::size_of::<[f32; 3]>() as wgpu::BufferAddress,
                    shader_location: 1,
                    format: wgpu::VertexFormat::Float32x2,
                },
                wgpu::VertexAttribute {
                    offset: mem::size_of::<[f32; 5]>() as wgpu::BufferAddress,
                    shader_location: 2,
                    format: wgpu::VertexFormat::Float32x3,
                },
                wgpu::VertexAttribute {
                    offset: mem::size_of::<[f32; 8]>() as wgpu::BufferAddress,
                    shader_location: 3,
                    format: wgpu::VertexFormat::Float32x3,
                },
                wgpu::VertexAttribute {
                    offset: mem::size_of::<[f32; 11]>() as wgpu::BufferAddress,
                    shader_location: 4,
                    format: wgpu::VertexFormat::Float32x3,
                },
            ],
        }
    }
}

pub struct Material {
    pub bind_group: wgpu::BindGroup,
}

impl Material {
    pub fn new(
        device: &wgpu::Device,
        layout: &wgpu::BindGroupLayout,
        diffuse: &Texture,
        normal: &Texture,
    ) -> Self {
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("material_bind_group"),
            layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&diffuse.view),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&diffuse.sampler),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::TextureView(&normal.view),
                },
                wgpu::BindGroupEntry {
                    binding: 3,
                    resource: wgpu::BindingResource::Sampler(&normal.sampler),
                },
            ],
        });
        Self { bind_group }
    }

    // Diffuse texture + sampler at 0/1, normal map + sampler at 2/3
    pub fn bind_group_layout(device: &wgpu::Device) -> wgpu::BindGroupLayout {
        let texture = |binding| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                multisampled: false,
                view_dimension: wgpu::TextureViewDimension::D2,
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
            },
            count: None,
        };
        let sampler = |binding| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
            count: None,
        };
        device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("material_bind_group_layout"),
            entries: &[texture(0), sampler(1), texture(2), sampler(3)],
        })
    }
}

pub struct Mesh {
    pub vertex_buffer: wgpu::Buffer,
    pub index_buffer: wgpu::Buffer,
    pub num_elements: u32,
    pub material: usize,
}

pub struct Model {
    pub meshes: Vec<Mesh>,
    pub materials: Vec<Material>,
}

impl Model {
    // One mesh with one flat-color material: 1x1 diffuse of `color` (sRGB 0..1) + 1x1 flat normal map
    pub fn from_mesh(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        layout: &wgpu::BindGroupLayout,
        name: &str,
        vertices: &[ModelVertex],
        indices: &[u32],
        color: [f32; 4],
    ) -> anyhow::Result<Self> {
        Self::from_parts(device, queue, layout, name, &[(vertices, indices, color)])
    }

    // Several meshes, each with its own flat color (a mesh per material)
    pub fn from_parts(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        layout: &wgpu::BindGroupLayout,
        name: &str,
        parts: &[(&[ModelVertex], &[u32], [f32; 4])],
    ) -> anyhow::Result<Self> {
        let normal = Texture::from_rgba8_1x1(device, queue, [128, 128, 255, 255], true)?;
        let mut model = Self {
            meshes: Vec::new(),
            materials: Vec::new(),
        };
        for (i, &(vertices, indices, color)) in parts.iter().enumerate() {
            let diffuse = Texture::from_rgba8_1x1(
                device,
                queue,
                color.map(|c| (c * 255.0).round() as u8),
                false,
            )?;
            model.meshes.push(Mesh {
                vertex_buffer: device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some(&format!("{name} Vertex Buffer")),
                    contents: bytemuck::cast_slice(vertices),
                    usage: wgpu::BufferUsages::VERTEX,
                }),
                index_buffer: device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some(&format!("{name} Index Buffer")),
                    contents: bytemuck::cast_slice(indices),
                    usage: wgpu::BufferUsages::INDEX,
                }),
                num_elements: indices.len() as u32,
                material: i,
            });
            model
                .materials
                .push(Material::new(device, layout, &diffuse, &normal));
        }
        Ok(model)
    }
}

// Tangent and bitangent point along +u and +v of the texture on each triangle's surface.
// Together with the normal they form the TBN matrix the shader uses to read the normal map.
// Vertices shared by several triangles get the average.
pub(crate) fn compute_tangents(vertices: &mut [ModelVertex], indices: &[u32]) {
    use cgmath::{InnerSpace, Vector2, Vector3};

    let mut triangles_included = vec![0u32; vertices.len()];

    for tri in indices.as_chunks::<3>().0 {
        let [v0, v1, v2] = [0, 1, 2].map(|k| vertices[tri[k] as usize]);

        let pos0: Vector3<f32> = v0.position.into();
        let pos1: Vector3<f32> = v1.position.into();
        let pos2: Vector3<f32> = v2.position.into();
        let uv0: Vector2<f32> = v0.tex_coords.into();
        let uv1: Vector2<f32> = v1.tex_coords.into();
        let uv2: Vector2<f32> = v2.tex_coords.into();

        // Triangle edges in model space and in uv space
        let delta_pos1 = pos1 - pos0;
        let delta_pos2 = pos2 - pos0;
        let delta_uv1 = uv1 - uv0;
        let delta_uv2 = uv2 - uv0;

        // Solve delta_pos = delta_uv.x * T + delta_uv.y * B for T and B
        let r = 1.0 / (delta_uv1.x * delta_uv2.y - delta_uv1.y * delta_uv2.x);
        let tangent = (delta_pos1 * delta_uv2.y - delta_pos2 * delta_uv1.y) * r;
        // Negated because wgpu's v points down while the normal map's green channel points up
        let bitangent = (delta_pos2 * delta_uv1.x - delta_pos1 * delta_uv2.x) * -r;

        for &i in tri {
            let v = &mut vertices[i as usize];
            v.tangent = (tangent + Vector3::from(v.tangent)).into();
            v.bitangent = (bitangent + Vector3::from(v.bitangent)).into();
            triangles_included[i as usize] += 1;
        }
    }

    for (v, &n) in vertices.iter_mut().zip(&triangles_included) {
        if n == 0 {
            continue;
        }
        let n = n as f32;
        v.tangent = (Vector3::from(v.tangent) / n).normalize().into();
        v.bitangent = (Vector3::from(v.bitangent) / n).normalize().into();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vertex(position: [f32; 3], tex_coords: [f32; 2]) -> ModelVertex {
        ModelVertex {
            position,
            tex_coords,
            normal: [0.0, 0.0, 1.0],
            tangent: [0.0; 3],
            bitangent: [0.0; 3],
        }
    }

    #[test]
    fn tangents_follow_uv_axes() {
        // Quad facing +Z, u grows along +X, v (wgpu, pointing down) grows along -Y
        let mut vertices = vec![
            vertex([0.0, 0.0, 0.0], [0.0, 1.0]),
            vertex([1.0, 0.0, 0.0], [1.0, 1.0]),
            vertex([1.0, 1.0, 0.0], [1.0, 0.0]),
            vertex([0.0, 1.0, 0.0], [0.0, 0.0]),
        ];
        compute_tangents(&mut vertices, &[0, 1, 2, 0, 2, 3]);

        for v in &vertices {
            assert!((v.tangent[0] - 1.0).abs() < 1e-5, "{v:?}");
            // Bitangent points "up" in the image, matching the normal map's green channel
            assert!((v.bitangent[1] - 1.0).abs() < 1e-5, "{v:?}");
        }
    }
}

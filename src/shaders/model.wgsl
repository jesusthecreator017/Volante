struct InstanceInput {
  @location(5) model_matrix_0: vec4<f32>,
  @location(6) model_matrix_1: vec4<f32>,
  @location(7) model_matrix_2: vec4<f32>,
  @location(8) model_matrix_3: vec4<f32>,
  @location(9) normal_matrix_0: vec3<f32>,
  @location(10) normal_matrix_1: vec3<f32>,
  @location(11) normal_matrix_2: vec3<f32>,
}

struct CameraUniform {
  view_pos: vec4<f32>,
  view_proj: mat4x4<f32>,
};

@group(1) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
  @location(0) position: vec3<f32>,
  @location(1) tex_coords: vec2<f32>,
  @location(2) normal: vec3<f32>,
  @location(3) tangent: vec3<f32>,
  @location(4) bitangent: vec3<f32>,
};

// Lighting is done in world space (lighting.wgsl, prepended at build time)
struct VertexOutput {
  @builtin(position) clip_position: vec4<f32>,
  @location(0) tex_coords: vec2<f32>,
  @location(1) world_position: vec3<f32>,
  @location(2) world_normal: vec3<f32>,
  @location(3) world_tangent: vec3<f32>,
  @location(4) world_bitangent: vec3<f32>,
};

@vertex
fn vs_main(model: VertexInput, instance: InstanceInput) -> VertexOutput {
  let model_matrix = mat4x4<f32>(
    instance.model_matrix_0,
    instance.model_matrix_1,
    instance.model_matrix_2,
    instance.model_matrix_3,
  );
  let normal_matrix = mat3x3<f32>(
    instance.normal_matrix_0,
    instance.normal_matrix_1,
    instance.normal_matrix_2,
  );

  let world_position = model_matrix * vec4<f32>(model.position, 1.0);

  var out: VertexOutput;
  out.clip_position = camera.view_proj * world_position;
  out.tex_coords = model.tex_coords;
  out.world_position = world_position.xyz;
  out.world_normal = normal_matrix * model.normal;
  out.world_tangent = normal_matrix * model.tangent;
  out.world_bitangent = normal_matrix * model.bitangent;

  return out;
}

@group(0) @binding(0) var t_diffuse: texture_2d<f32>;
@group(0) @binding(1) var s_diffuse: sampler;
@group(0) @binding(2) var t_normal: texture_2d<f32>;
@group(0) @binding(3) var s_normal: sampler;

// Materials with alpha below this are emissive: unlit, rgb scaled into HDR (light fixtures)
// ponytail: alpha doubles as the emissive flag because materials are 1x1 flat colors; a real emissive map needs its own binding.
const EMISSIVE_ALPHA: f32 = 0.9;
const EMISSIVE_GAIN: f32 = 6.0;

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
  let object_color = textureSample(t_diffuse, s_diffuse, in.tex_coords);
  let object_normal = textureSample(t_normal, s_normal, in.tex_coords);
  // After both samples: texture sampling must stay in uniform control flow
  if (object_color.a < EMISSIVE_ALPHA) {
    return vec4<f32>(object_color.rgb * EMISSIVE_GAIN, 1.0);
  }

  // Normal map stores [-1, 1] tangent-space directions packed into [0, 1] colors.
  // TBN rotates them into world space. Renormalize: interpolation shortens the vectors.
  let tbn = mat3x3<f32>(
    normalize(in.world_tangent),
    normalize(in.world_bitangent),
    normalize(in.world_normal),
  );
  let normal = normalize(tbn * (object_normal.xyz * 2.0 - 1.0));

  return vec4<f32>(shade(object_color.rgb, normal, in.world_position, camera.view_pos.xyz), object_color.a);
}

// Linear blend skinning over a uniform joint palette (no storage buffers: WebGL2). Lit like model.wgsl with a flat color (lighting.wgsl is prepended at build time).
struct CameraUniform {
  view_pos: vec4<f32>,
  view_proj: mat4x4<f32>,
};

// Keep in sync with PlayerUniform in renderer/skinned.rs
struct Player {
  color: vec4<f32>,
  joints: array<mat4x4<f32>, 32>,
};

@group(0) @binding(0) var<uniform> player: Player;
@group(1) @binding(0) var<uniform> camera: CameraUniform;

struct VertexInput {
  @location(0) position: vec3<f32>,
  @location(1) normal: vec3<f32>,
  // Joint indices as floats (attribute compatibility), weights sum to 1
  @location(2) joints: vec4<f32>,
  @location(3) weights: vec4<f32>,
};

struct VertexOutput {
  @builtin(position) clip_position: vec4<f32>,
  @location(0) world_position: vec3<f32>,
  @location(1) world_normal: vec3<f32>,
};

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
  let skin = player.joints[u32(in.joints.x)] * in.weights.x
    + player.joints[u32(in.joints.y)] * in.weights.y
    + player.joints[u32(in.joints.z)] * in.weights.z
    + player.joints[u32(in.joints.w)] * in.weights.w;
  let world_position = skin * vec4<f32>(in.position, 1.0);

  var out: VertexOutput;
  out.clip_position = camera.view_proj * world_position;
  out.world_position = world_position.xyz;
  // The palette carries a uniform scale, so renormalize instead of an inverse transpose
  out.world_normal = normalize((skin * vec4<f32>(in.normal, 0.0)).xyz);
  return out;
}

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
  let normal = normalize(in.world_normal);
  return vec4<f32>(shade(player.color.rgb, normal, in.world_position, camera.view_pos.xyz), 1.0);
}

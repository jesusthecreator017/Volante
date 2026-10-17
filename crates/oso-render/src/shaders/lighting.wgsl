// Indoor lighting shared by model.wgsl and skinned.wgsl (concatenated before them at build time).
// Binding: group 2. Keep Light / Lights in sync with scene/light.rs.
struct Light {
  position: vec3<f32>,
  range: f32,
  color: vec3<f32>,
};

struct Lights {
  count: u32,
  lights: array<Light, 8>,
};

@group(2) @binding(0) var<uniform> lights: Lights;

// Keep in sync with scene/light.rs `attenuation`: inverse square, full inside 1 m, smoothly 0 at range
fn attenuation(distance: f32, range: f32) -> f32 {
  let ratio = distance / range;
  let window = clamp(1.0 - ratio * ratio * ratio * ratio, 0.0, 1.0);
  return window * window / max(distance * distance, 1.0);
}

// Hemisphere ambient: ceiling color from above, floor bounce from below, blended by normal.y
fn hemisphere_ambient(normal: vec3<f32>) -> vec3<f32> {
  let ceiling = vec3<f32>(0.62, 0.65, 0.72);
  let floor_bounce = vec3<f32>(0.36, 0.33, 0.28);
  return mix(floor_bounce, ceiling, normal.y * 0.5 + 0.5);
}

// Ambient + Blinn-Phong diffuse/specular summed over every light, times the surface color
fn shade(albedo: vec3<f32>, normal: vec3<f32>, world_position: vec3<f32>, view_pos: vec3<f32>) -> vec3<f32> {
  let view_dir = normalize(view_pos - world_position);
  var total = hemisphere_ambient(normal);
  for (var i = 0u; i < lights.count; i = i + 1u) {
    let light = lights.lights[i];
    let to_light = light.position - world_position;
    let distance = length(to_light);
    let light_dir = to_light / max(distance, 1e-4);
    let half_dir = normalize(view_dir + light_dir);
    let diffuse = max(dot(normal, light_dir), 0.0);
    let specular = pow(max(dot(normal, half_dir), 0.0), 32.0);
    total += light.color * attenuation(distance, light.range) * (diffuse + specular);
  }
  return total * albedo;
}

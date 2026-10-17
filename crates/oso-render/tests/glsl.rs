// WebGL2 risk: translate the scene shaders to GLSL ES 3.00 with the same naga that wgpu's GL backend uses.
use naga::back::glsl;
use naga::valid::{Capabilities, ValidationFlags, Validator};

fn translate(src: &str, stage: naga::ShaderStage, entry: &str) {
    let module =
        naga::front::wgsl::parse_str(src).unwrap_or_else(|e| panic!("{}", e.emit_to_string(src)));
    let info = Validator::new(ValidationFlags::all(), Capabilities::all())
        .validate(&module)
        .unwrap();
    let options = glsl::Options {
        version: glsl::Version::Embedded {
            version: 300,
            is_webgl: true,
        },
        ..Default::default()
    };
    let pipeline = glsl::PipelineOptions {
        shader_stage: stage,
        entry_point: entry.into(),
        multiview: None,
    };
    let mut out = String::new();
    glsl::Writer::new(
        &mut out,
        &module,
        &info,
        &options,
        &pipeline,
        naga::proc::BoundsCheckPolicies::default(),
    )
    .unwrap()
    .write()
    .unwrap();
    assert!(out.contains("#version 300 es"));
}

fn entries(src: &str) -> Vec<(naga::ShaderStage, String)> {
    let m = naga::front::wgsl::parse_str(src).unwrap();
    m.entry_points
        .iter()
        .map(|e| (e.stage, e.name.clone()))
        .collect()
}

#[test]
fn scene_shaders_translate_to_webgl2_glsl() {
    let lighting = include_str!("../src/shaders/lighting.wgsl");
    let shaders = [
        format!("{lighting}{}", include_str!("../src/shaders/model.wgsl")),
        format!("{lighting}{}", include_str!("../src/shaders/skinned.wgsl")),
        include_str!("../src/shaders/lines.wgsl").to_string(),
        include_str!("../src/shaders/hdr.wgsl").to_string(),
    ];
    for src in &shaders {
        let eps = entries(src);
        assert!(!eps.is_empty());
        for (stage, name) in eps {
            translate(src, stage, &name);
        }
    }
}

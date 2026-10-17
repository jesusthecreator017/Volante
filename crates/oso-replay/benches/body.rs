use criterion::{Criterion, criterion_group, criterion_main};
use oso_render::replay::Replay;
use oso_render::scene::body;
use oso_render::scene::rig::{PlayerRig, Rig, identity_palette};
use std::hint::black_box;

const SAMPLE: &[u8] = include_bytes!("../../../assets/replay/replay.bin");

fn bench(c: &mut Criterion) {
    let r = Replay::parse(SAMPLE).unwrap();
    let joints = r.sample(3.3, 0);
    let mut orient = body::BodyOrient::default();
    c.bench_function("part_transforms one player", |b| {
        b.iter(|| body::part_transforms(black_box(&joints), &mut orient))
    });
    let rig = Rig::parse(
        include_bytes!("../../../assets/models/humanoid/scene.gltf"),
        include_bytes!("../../../assets/models/humanoid/scene.bin"),
    )
    .unwrap();
    let mut player = PlayerRig::new(&rig, &body::Dims::measure(&r, 0));
    let mut palette = identity_palette();
    c.bench_function("rig retarget + palette one player", |b| {
        b.iter(|| rig.pose(black_box(&joints), &mut player, &mut palette))
    });
    c.bench_function("Replay::sample 2 players", |b| {
        b.iter(|| (r.sample(black_box(3.3), 0), r.sample(black_box(3.3), 1)))
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);

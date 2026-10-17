// Modules are private in the lib, so include the self-contained source directly.
#![allow(dead_code, unused_imports)]
#[path = "../src/replay.rs"]
mod replay;

use criterion::{Criterion, criterion_group, criterion_main};
use std::hint::black_box;

const SAMPLE: &[u8] = include_bytes!("../assets/replay/replay.bin");

fn parse(c: &mut Criterion) {
    c.bench_function("Replay::parse sample", |b| {
        b.iter(|| replay::Replay::parse(black_box(SAMPLE)).unwrap())
    });
}

// Per-frame viewer cost of the shuttle: sample + the polyline points the trail is built from
fn shuttle(c: &mut Criterion) {
    let r = replay::Replay::parse(SAMPLE).unwrap();
    let mut pts = Vec::new();
    let mut i = 0u32;
    c.bench_function("sample_shuttle", |b| {
        b.iter(|| {
            i = (i + 1) % 600;
            black_box(r.sample_shuttle(black_box(i as f64 / 60.0 + 0.004)))
        })
    });
    c.bench_function("shuttle_trail (0.6 s)", |b| {
        b.iter(|| {
            i = (i + 1) % 600;
            r.shuttle_trail(black_box(i as f64 / 60.0 + 0.004), 0.6, &mut pts);
            black_box(pts.len())
        })
    });
}

criterion_group!(benches, parse, shuttle);
criterion_main!(benches);

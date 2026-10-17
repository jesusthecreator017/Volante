use criterion::{Criterion, criterion_group, criterion_main};
use oso_render::replay::Replay;
use oso_replay::imu::fusion::{self, Params};
use oso_replay::imu::packet::{self, Sample};
use oso_replay::imu::swings;
use std::hint::black_box;

const SAMPLE: &[u8] = include_bytes!("../../../assets/replay/replay.bin");
// 10 minutes at 500 Hz
const N: usize = 300_000;

// 1 % lost, windows of up to 8 packets shuffled: the cost of the sort and dedupe path, not just the happy path
fn capture_bytes() -> Vec<u8> {
    let mut rng = 12345u64;
    let mut next = move || {
        rng = rng
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (rng >> 33) as usize
    };
    let mut packets: Vec<(u16, u32, [i16; 6])> = (0..N)
        .filter(|_| next() % 100 != 0)
        .map(|i| {
            (
                i as u16,
                (i * 2000) as u32,
                [0, 0, 2048, (i % 50) as i16, 0, 0],
            )
        })
        .collect();
    let mut i = 0;
    while i + 8 < packets.len() {
        let w = 2 + next() % 7;
        packets[i..i + w].reverse();
        i += w + next() % 20;
    }
    packet::encode(500, &packets)
}

fn samples() -> Vec<Sample> {
    (0..N)
        .map(|i| {
            let t = i as f64 * 0.002;
            Sample {
                t_dev: t,
                acc: [0.0, 0.0, 9.80665],
                gyro: [0.0, 0.0, (t * 3.0).sin() as f32],
                saturated: false,
            }
        })
        .collect()
}

// A 0.3 s, 800 dps swing every 2 s plus a 12 g impact spike in each: ~300 swings in 10 minutes
fn swing_samples() -> Vec<Sample> {
    (0..N)
        .map(|i| {
            let t = i as f64 * 0.002;
            let ph = t % 2.0;
            let w = if ph < 0.3 {
                800.0 * (ph / 0.3 * std::f64::consts::PI).sin()
            } else {
                5.0
            };
            let spike = if (i % 1000) == 80 {
                12.0 * 9.80665
            } else {
                0.0
            };
            Sample {
                t_dev: t,
                acc: [0.0, spike as f32, 9.80665],
                gyro: [w.to_radians() as f32, 0.0, 0.0],
                saturated: false,
            }
        })
        .collect()
}

fn bench(c: &mut Criterion) {
    let bytes = capture_bytes();
    c.bench_function(
        "packet::parse 300k packets (10 min, lossy, reordered)",
        |b| b.iter(|| packet::parse(black_box(&bytes)).unwrap()),
    );
    let s = samples();
    c.bench_function("fusion::run 300k samples", |b| {
        b.iter(|| fusion::run(black_box(&s), [0.0; 3], [0.0, 0.0, 9.8], Params::default()))
    });
    let sw = swing_samples();
    c.bench_function("swings::detect 300k samples (10 min, ~300 swings)", |b| {
        b.iter(|| swings::detect(black_box(&sw), &[], [0.0; 3], 0.0, 0.0, 500.0))
    });
    let mut q = [1.0, 0.0, 0.0, 0.0];
    c.bench_function("madgwick_step", |b| {
        b.iter(|| {
            fusion::madgwick_step(
                &mut q,
                black_box([0.1, 0.2, 0.3]),
                Some(black_box([0.0, 0.0, 9.8])),
                0.002,
                0.04,
            )
        })
    });
    let r = Replay::parse(SAMPLE).unwrap();
    c.bench_function("Replay::next_stroke", |b| {
        b.iter(|| r.next_stroke(black_box(3.3)))
    });
    c.bench_function("Replay::sample_racquet 1 player", |b| {
        b.iter(|| r.sample_racquet(black_box(3.3), 0))
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);

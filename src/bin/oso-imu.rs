//! Racquet IMU ingest: imu.bin + replay.bin (+ clap annotation) -> replay.bin with a `racquet` track.
//! cargo run --release --bin oso-imu -- --imu out/synth/imu.bin --replay assets/replay/replay.bin \
//!     --sync out/synth/sync.json --player 0 -o assets/replay/replay.bin
//! Without an IMU: `--replay R --no-imu -o out.bin` writes a contact-only `swings` track (no racquet track).

use anyhow::{Context, bail};
use oso::imu::correct::{self, YawMode};
use oso::imu::strokes::{self, STROKE_TYPES};
use oso::imu::{fusion, packet, pose, swings, sync};
use oso::replay::{Container, RACQUET_FIELDS, Replay, STROKE_FIELDS};
use serde_json::{Map, Value, json};
use std::path::PathBuf;

struct Args {
    /// None with --no-imu
    imu: Option<PathBuf>,
    replay: PathBuf,
    sync: Option<PathBuf>,
    player: usize,
    out: PathBuf,
    report: Option<PathBuf>,
    mode: YawMode,
    beta: f64,
    forward_only: bool,
}

const USAGE: &str = "usage: oso-imu (--imu imu.bin --sync sync.json | --no-imu) --replay replay.bin [--player N] -o out.bin \
                     [--no-video-yaw | --yaw-const] [--beta B] [--forward-only] [--report report.json]";

fn parse_args() -> anyhow::Result<Args> {
    let mut it = std::env::args().skip(1);
    let (mut imu, mut replay, mut sync, mut out, mut report) = (None, None, None, None, None);
    let (mut player, mut mode, mut beta) = (0usize, YawMode::Full, fusion::DEFAULT_BETA);
    let (mut forward_only, mut no_imu) = (false, false);
    while let Some(a) = it.next() {
        let mut val = |name: &str| {
            it.next()
                .with_context(|| format!("{name} needs a value\n{USAGE}"))
        };
        match a.as_str() {
            "--imu" => imu = Some(PathBuf::from(val(&a)?)),
            "--replay" => replay = Some(PathBuf::from(val(&a)?)),
            "--sync" => sync = Some(PathBuf::from(val(&a)?)),
            "-o" | "--out" => out = Some(PathBuf::from(val(&a)?)),
            "--report" => report = Some(PathBuf::from(val(&a)?)),
            "--player" => player = val(&a)?.parse()?,
            "--forward-only" => forward_only = true,
            "--no-imu" => no_imu = true,
            "--beta" => beta = val(&a)?.parse()?,
            "--no-video-yaw" => mode = YawMode::None,
            "--yaw-const" => mode = YawMode::ConstOnly,
            "-h" | "--help" => {
                println!("{USAGE}");
                std::process::exit(0);
            }
            _ => bail!("unknown argument {a}\n{USAGE}"),
        }
    }
    let need = |v: Option<PathBuf>, n: &str| v.with_context(|| format!("missing {n}\n{USAGE}"));
    if no_imu && (imu.is_some() || sync.is_some()) {
        bail!("--no-imu excludes --imu/--sync\n{USAGE}");
    }
    let (imu, sync) = if no_imu {
        (None, None)
    } else {
        (Some(need(imu, "--imu")?), Some(need(sync, "--sync")?))
    };
    Ok(Args {
        imu,
        replay: need(replay, "--replay")?,
        sync,
        out: need(out, "-o")?,
        report,
        player,
        mode,
        beta,
        forward_only,
    })
}

fn print_strokes(list: &[strokes::Stroke]) {
    let f = |v: Option<f64>, p: usize| v.map_or("-".to_string(), |v| format!("{v:.p$}"));
    println!(
        "strokes: {} ({} with a contact, {} air swings)",
        list.len(),
        list.iter().filter(|s| s.t_contact.is_some()).count(),
        list.iter().filter(|s| s.t_contact.is_none()).count()
    );
    println!(
        "  {:>7} {:>7} {:>2} {:<10} {:>7} {:>7} {:>6} {:>7} {:>6} {:>4} | {:>5} {:>6} {:>6} {:>5} {:>6} {:>6} {:>5}",
        "t_peak",
        "t_cont",
        "pl",
        "type",
        "peak dps",
        "head m/s",
        "rot",
        "impact",
        "tim ms",
        "sat",
        "h",
        "v0",
        "angle",
        "apex",
        "x_ct",
        "x_end",
        "first"
    );
    for s in list {
        let w = s.swing.as_ref();
        let r = s.rules.as_ref();
        println!(
            "  {:>7.3} {:>7} {:>2} {:<10} {:>7} {:>7} {:>6} {:>7} {:>6} {:>4} | {:>5} {:>6} {:>6} {:>5} {:>6} {:>6} {:>5}",
            s.t_key(),
            f(s.t_contact, 3),
            s.player,
            STROKE_TYPES[s.kind as usize],
            f(w.map(|w| w.peak_dps), 0),
            f(s.head_speed_peak, 1),
            f(w.map(|w| w.head_speed_rot()), 1),
            f(w.and_then(|w| w.impact).map(|i| i.1), 1),
            f(w.and_then(|w| w.timing_ms()), 1),
            w.map_or("-", |w| if w.saturated { "yes" } else { "no" }),
            f(r.map(|r| r.h), 2),
            f(r.map(|r| r.v0), 1),
            f(r.map(|r| r.angle_deg), 1),
            f(r.map(|r| r.apex), 2),
            f(r.map(|r| r.x_contact), 2),
            f(r.map(|r| r.x_end), 2),
            r.map_or("-", |r| if r.first_of_rally { "yes" } else { "no" }),
        );
    }
}

/// Sets the `swings` track and writes the file.
fn write_strokes(
    mut c: Container,
    list: &[strokes::Stroke],
    out: &std::path::Path,
) -> anyhow::Result<()> {
    let rows: Vec<f32> = list.iter().flat_map(|s| s.row()).collect();
    let mut meta = Map::new();
    meta.insert("name".into(), "swings".into());
    meta.insert(
        "layout".into(),
        "t_start,t_peak,t_end,t_contact,player,stroke_type,peak_omega_dps,head_speed_peak,head_speed_rot,impact_g,timing_ms,contact_h,launch_speed,launch_angle_deg,saturated,confidence".into(),
    );
    meta.insert("shape".into(), json!([list.len(), STROKE_FIELDS]));
    meta.insert("per_frame".into(), false.into());
    meta.insert("stroke_types".into(), json!(STROKE_TYPES));
    c.set_track(meta, &rows);
    std::fs::write(out, c.write())?;
    Ok(())
}

fn main() -> anyhow::Result<()> {
    let args = parse_args()?;
    let (Some(imu_path), Some(sync_path)) = (&args.imu, &args.sync) else {
        // --no-imu: every shuttle contact becomes a contact-only row, rule-labelled
        let bytes =
            std::fs::read(&args.replay).with_context(|| args.replay.display().to_string())?;
        let replay = Replay::parse(&bytes)?;
        let list = strokes::build(&[], args.player, &[], &replay);
        print_strokes(&list);
        write_strokes(Container::read(&bytes)?, &list, &args.out)?;
        println!(
            "wrote {}: swings track, {} contact rows (no racquet track)",
            args.out.display(),
            list.len()
        );
        return Ok(());
    };
    let cap =
        packet::parse(&std::fs::read(imu_path).with_context(|| imu_path.display().to_string())?)?;
    let s = cap.stats;
    println!(
        "packets: {} read, {} unique, {} lost, {} duplicate, {} reordered ({} gaps)",
        s.arrivals,
        s.unique,
        s.lost,
        s.duplicate,
        s.reordered,
        cap.gaps.len()
    );
    let saturated = cap.samples.iter().filter(|x| x.saturated).count();
    println!("saturated gyro samples: {saturated}");

    let replay_bytes =
        std::fs::read(&args.replay).with_context(|| args.replay.display().to_string())?;
    let replay = Replay::parse(&replay_bytes)?;
    anyhow::ensure!(
        args.player < replay.players,
        "player {} not in replay ({} players)",
        args.player,
        replay.players
    );
    let claps_video: Vec<f64> = {
        let v: Value = serde_json::from_slice(&std::fs::read(sync_path)?)?;
        // {"claps_video": [start, end]} or the older single {"t_clap_video": t}
        match v.get("claps_video").and_then(Value::as_array) {
            Some(a) => a.iter().filter_map(Value::as_f64).collect(),
            None => vec![
                v["t_clap_video"]
                    .as_f64()
                    .context("sync.json needs claps_video or t_clap_video")?,
            ],
        }
    };
    anyhow::ensure!(!claps_video.is_empty(), "sync.json has no clap times");

    let params = fusion::Params {
        beta: args.beta,
        two_pass: !args.forward_only,
        nominal_dt: 1.0 / cap.rate_hz.max(1) as f64,
    };
    let (track, bias, win) = fusion::calibrate_and_run(&cap.samples, params)
        .context("no stationary window of >= 1 s in the IMU data")?;
    let bias_dps = bias.map(f64::to_degrees);
    println!(
        "stationary window: {:.2} .. {:.2} s (device clock); gyro bias {:.3} {:.3} {:.3} dps",
        cap.samples[win.start].t_dev,
        cap.samples[win.end - 1].t_dev,
        bias_dps[0],
        bias_dps[1],
        bias_dps[2]
    );

    let r = sync::sync(&cap.samples, win.end, &claps_video, &replay, args.player)
        .context("no clap spike (> 6 g) after the stationary window")?;
    println!("sync: t_video = t_dev * (1 + a) + b");
    println!("  start-clap-only offset b = {:.4} s", r.b_clap);
    if r.drift_known {
        println!(
            "  fitted (claps) offset b = {:.4} s, drift a = {:.1} ppm",
            r.b,
            r.a * 1e6
        );
    } else {
        println!(
            "  offset b = {:.4} s, drift unknown (only {} clap matched; a = 0)",
            r.b,
            r.claps.len()
        );
    }
    println!("  impact check vs: {}", r.contact_source.label());
    println!("  {:>10} {:>10} {:>10}", "t_dev (s)", "t_video", "resid ms");
    for (i, c) in r.claps.iter().enumerate() {
        let resid = (c.0 * (1.0 + r.a) + r.b - c.1) * 1e3;
        println!(
            "  {:>10.4} {:>10.4} {:>+10.2}  clap {} (fit anchor)",
            c.0,
            c.1,
            resid,
            i + 1
        );
    }
    for p in &r.impacts {
        println!(
            "  {:>10.4} {:>10.4} {:>+10.2}  impact (check)",
            p.t_dev, p.t_video, p.residual_ms
        );
    }
    println!(
        "  {} impacts vs the clap fit: residual RMS {:.2} ms, max {:.2} ms",
        r.impacts.len(),
        r.rms_ms,
        r.max_ms
    );

    let out = pose::build(&replay, args.player, &track, r.a, r.b, args.mode);
    let dev: Vec<f64> = out
        .yaw
        .per_frame
        .iter()
        .map(|&y| correct::angle_diff(y, out.yaw.constant).to_degrees().abs())
        .collect();
    println!(
        "yaw correction ({:?}): constant {:.1} deg from {} frames; drift tracking |y - const| mean {:.2} deg, max {:.2} deg",
        args.mode,
        out.yaw.constant.to_degrees(),
        out.yaw.frames_used,
        dev.iter().sum::<f64>() / dev.len().max(1) as f64,
        dev.iter().cloned().fold(0.0, f64::max)
    );

    let mut c = Container::read(&replay_bytes)?;
    let players: Value = c
        .tracks
        .iter()
        .find(|t| t.0.get("name") == Some(&json!("joints3d")))
        .and_then(|t| t.0.get("players").cloned())
        .unwrap_or_else(|| json!([]));
    let mut meta = Map::new();
    meta.insert("name".into(), "racquet".into());
    meta.insert(
        "layout".into(),
        "px,py,pz,qx,qy,qz,qw,conf,head_speed".into(),
    );
    meta.insert(
        "shape".into(),
        json!([replay.frame_count, replay.players, RACQUET_FIELDS]),
    );
    meta.insert("players".into(), players);
    meta.insert(
        "sync".into(),
        json!({
            "offset_s": r.b,
            "drift_ppm": r.a * 1e6,
            "drift_known": r.drift_known,
            "residual_rms_ms": r.rms_ms,
            "residual_max_ms": r.max_ms,
            "n_impacts": r.impacts.len(),
        }),
    );
    c.set_track(meta, &out.data);

    // Swings from the gyro, stroke events from swings + the replay's shuttle contacts
    let head_speed: Vec<f32> = (0..replay.frame_count)
        .map(|f| {
            let e =
                &out.data[(f * replay.players + args.player) * RACQUET_FIELDS..][..RACQUET_FIELDS];
            if e[7] > 0.0 { e[8] } else { f32::NAN }
        })
        .collect();
    let sw = swings::detect(&cap.samples, &cap.gaps, bias, r.a, r.b);
    let list = strokes::build(&sw, args.player, &head_speed, &replay);
    print_strokes(&list);
    write_strokes(c, &list, &args.out)?;
    let shown = out
        .data
        .chunks(RACQUET_FIELDS)
        .filter(|e| e[7] > 0.0)
        .count();
    println!(
        "wrote {}: racquet track for player {} ({} frames with conf > 0)",
        args.out.display(),
        args.player,
        shown
    );

    if let Some(path) = &args.report {
        let strokes_json: Vec<Value> = list
            .iter()
            .map(|s| json!({ "row": s.row().map(|v| if v.is_finite() { json!(v) } else { Value::Null }) }))
            .collect();
        let report = json!({
            "packets": { "arrivals": s.arrivals, "unique": s.unique, "lost": s.lost, "duplicate": s.duplicate,
                         "reordered": s.reordered },
            "saturated_samples": saturated,
            "bias_dps": bias_dps,
            "sync": { "a": r.a, "b": r.b, "b_clap": r.b_clap, "drift_known": r.drift_known,
                      "rms_ms": r.rms_ms, "max_ms": r.max_ms, "n_impacts": r.impacts.len(),
                      "contact_source": r.contact_source.label(),
                      "claps": r.claps.iter().map(|c| json!({ "t_dev": c.0, "t_video": c.1 })).collect::<Vec<_>>(),
                      "pairs": r.impacts.iter().map(|p| json!({ "t_dev": p.t_dev, "t_video": p.t_video,
                          "residual_ms": p.residual_ms })).collect::<Vec<_>>() },
            "strokes": strokes_json,
            "yaw": { "constant_deg": out.yaw.constant.to_degrees(), "frames_used": out.yaw.frames_used },
        });
        std::fs::write(path, serde_json::to_vec_pretty(&report)?)?;
    }
    Ok(())
}

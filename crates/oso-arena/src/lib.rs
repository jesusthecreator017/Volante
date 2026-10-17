//! Arena viewer: the shared renderer plus the scoreboard, driven by the `point` events of a replay.bin.

mod racquet;
mod scoreboard;
#[cfg(target_arch = "wasm32")]
mod web_api;

use oso_render::Extras;
use oso_render::renderer::model::Model;
use oso_render::replay::Replay;
use oso_render::scene::components::{ModelHandle, Transform};
use oso_render::scene::court;
use oso_render::state::PLAYER_COLORS;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

const BOARD_COLOR: [f32; 4] = [0.03, 0.03, 0.04, 1.0];
// Lit segments are emissive (alpha < 0.9, see model.wgsl); the separator is white
const SEGMENT_ALPHA: f32 = 0.5;

/// One 7-segment bar of the scoreboard: `cell` 0..4 (A tens, A units, B tens, B units; by slot P1 / P2 when the replay has no `match` header), `seg` 0..7 (a..g)
#[derive(Copy, Clone, Debug)]
struct ScoreSegment {
    cell: usize,
    seg: usize,
}

#[derive(Default)]
struct ArenaExtras {
    // Lit-segment masks currently written to the scoreboard instances; None = not written yet (or no scoreboard)
    score_masks: Option<[u8; 4]>,
}

impl Extras for ArenaExtras {
    #[cfg(target_arch = "wasm32")]
    fn replay_loaded(&mut self, r: &Replay) {
        web_api::set_replay(r);
    }

    // A model keeps its color across a change of ends (the replay slots follow the world sides)
    fn slot_colors(&self, r: &Replay, t: f64) -> [usize; 2] {
        r.slot_models(t)
    }

    // Procedural racquet: the Arena bundle ships no racquet OBJ
    fn racquet_parts(
        &self,
        color: [f32; 3],
    ) -> Option<Vec<(oso_render::scene::court::CpuMesh, [f32; 4])>> {
        Some(racquet::parts(color))
    }

    fn spawn(
        &mut self,
        world: &mut hecs::World,
        models: &mut Vec<Model>,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        layout: &wgpu::BindGroupLayout,
        r: &Replay,
    ) -> anyhow::Result<()> {
        // Only a replay with point events (a match) has a scoreboard
        if r.score_at(0.0).is_some() {
            spawn_scoreboard(world, models, device, queue, layout)?;
        }
        Ok(())
    }

    // Score is stateless in t, so seeks work; instances are only touched when the lit segments change. The board shows the score by model
    // (A left in orange, B right in cyan, like the page header), not by court side, so it does not swap at a change of ends.
    fn update(&mut self, world: &mut hecs::World, r: &Replay, t: f64) {
        if let Some(score) = r.model_score_at(t) {
            let masks = scoreboard::masks(score);
            if self.score_masks != Some(masks) {
                self.score_masks = Some(masks);
                for (t, seg) in world.query_mut::<(&mut Transform, &ScoreSegment)>() {
                    t.scale = if masks[seg.cell] >> seg.seg & 1 == 1 {
                        1.0
                    } else {
                        0.0
                    };
                }
            }
        }
    }
}

// The scoreboard: dark board hung on cables behind the far baseline, 4 digit cells of 7 bar instances each (hidden until the first update writes the score),
// and a two-dot separator. Bars are model A orange / model B cyan emissive (PLAYER_COLORS 0 / 1).
fn spawn_scoreboard(
    world: &mut hecs::World,
    models: &mut Vec<Model>,
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
) -> anyhow::Result<()> {
    let id = cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0);
    let mut add =
        |name: &str, (v, i): court::CpuMesh, color: [f32; 4]| -> anyhow::Result<ModelHandle> {
            models.push(Model::from_mesh(
                device, queue, layout, name, &v, &i, color,
            )?);
            Ok(ModelHandle(models.len() - 1))
        };
    let board = add("Scoreboard", scoreboard::board(), BOARD_COLOR)?;
    world.spawn((
        Transform {
            position: cgmath::Vector3::new(0.0, 0.0, 0.0),
            rotation: id,
            scale: 1.0,
        },
        board,
    ));
    let glow = |p: usize| {
        let [r, g, b] = PLAYER_COLORS[p];
        [r, g, b, SEGMENT_ALPHA]
    };
    let dot = add(
        "Scoreboard dot",
        scoreboard::dot(),
        [1.0, 1.0, 1.0, SEGMENT_ALPHA],
    )?;
    for y in scoreboard::DOT_Y {
        world.spawn((
            Transform {
                position: scoreboard::world_pos(0.0, y, 0.025),
                rotation: id,
                scale: 1.0,
            },
            dot,
        ));
    }
    for p in 0..2 {
        let h = add("Scoreboard bar h", scoreboard::bar_h(), glow(p))?;
        let v = add("Scoreboard bar v", scoreboard::bar_v(), glow(p))?;
        for cell in 2 * p..2 * p + 2 {
            for seg in 0..7 {
                world.spawn((
                    Transform {
                        position: scoreboard::segment_world_pos(cell, seg),
                        rotation: id,
                        scale: 0.0,
                    },
                    if scoreboard::is_horizontal(seg) { h } else { v },
                    ScoreSegment { cell, seg },
                ));
            }
        }
    }
    Ok(())
}

#[cfg(target_arch = "wasm32")]
#[wasm_bindgen(start)]
pub fn run_web() -> anyhow::Result<(), wasm_bindgen::JsValue> {
    console_error_panic_hook::set_once();
    run().unwrap_throw();
    Ok(())
}

pub fn run() -> anyhow::Result<()> {
    // No `--replay`: the latest simulated match if there is one, else the synthetic replay
    #[cfg(not(target_arch = "wasm32"))]
    if !std::env::args().any(|a| a == "--replay") {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let latest = root.join("out/badsim/match.bin");
        let _ = oso_render::assets::REPLAY_OVERRIDE.set(if latest.exists() {
            latest
        } else {
            root.join("assets/replay/replay.synthetic.bin")
        });
    }
    oso_render::run(Box::<ArenaExtras>::default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use oso_render::replay::{Build, MatchInfo, ModelInfo};

    fn replay_with_ends(ends: Vec<(f64, u8)>) -> Replay {
        let b = || ModelInfo {
            name: "M".into(),
            build: Build {
                power: 1,
                speed: 1,
                reach: 1,
                accuracy: 1,
            },
        };
        Replay::from_parts(60.0, 1, 2, vec![[0.0; 4]; 2 * 17], None).with_match(MatchInfo {
            a: b(),
            b: b(),
            ends,
        })
    }

    // The colors of the slots swap at a change of ends so model A stays orange (PLAYER_COLORS[0]) and B cyan
    #[test]
    fn slot_colors_follow_the_models_across_a_change_of_ends() {
        let r = replay_with_ends(vec![(0.0, 0), (100.0, 1)]);
        let x = ArenaExtras::default();
        assert_eq!(x.slot_colors(&r, 99.9), [0, 1]);
        assert_eq!(x.slot_colors(&r, 100.0), [1, 0]);
        assert_eq!(x.slot_colors(&r, 500.0), [1, 0]);
        // Without a match header (an old file) the colors stay by slot
        let plain = Replay::from_parts(60.0, 1, 2, vec![[0.0; 4]; 2 * 17], None);
        assert_eq!(x.slot_colors(&plain, 500.0), [0, 1]);
    }

    #[test]
    fn arena_brings_its_own_racquet_one_tint_per_player_color() {
        let x = ArenaExtras::default();
        let a = x.racquet_parts(PLAYER_COLORS[0]).unwrap();
        let b = x.racquet_parts(PLAYER_COLORS[1]).unwrap();
        assert_eq!(a.len(), b.len());
        assert_ne!(a[0].1, b[0].1);
    }

    // The default replay of a fresh checkout must exist
    #[test]
    fn synthetic_replay_is_there() {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../assets/replay/replay.synthetic.bin");
        assert!(p.exists());
    }
}

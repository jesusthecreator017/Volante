//! Arena viewer: the shared renderer plus the scoreboard, driven by the `point` events of a replay.bin.

mod scoreboard;
#[cfg(target_arch = "wasm32")]
mod web_api;

use oso_render::Extras;
use oso_render::replay::Replay;
use oso_render::renderer::model::Model;
use oso_render::scene::components::{ModelHandle, Transform};
use oso_render::scene::court;
use oso_render::state::PLAYER_COLORS;

#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;

const BOARD_COLOR: [f32; 4] = [0.03, 0.03, 0.04, 1.0];
// Lit segments are emissive (alpha < 0.9, see model.wgsl); the separator is white
const SEGMENT_ALPHA: f32 = 0.5;

/// One 7-segment bar of the scoreboard: `cell` 0..4 (P1 tens, P1 units, P2 tens, P2 units), `seg` 0..7 (a..g)
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
        web_api::set_points(r.events());
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

    // Score is stateless in t, so seeks work; instances are only touched when the lit segments change
    fn update(&mut self, world: &mut hecs::World, r: &Replay, t: f64) {
        if let Some(score) = r.score_at(t) {
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
// and a two-dot separator. Bars are P0 orange / P1 cyan emissive.
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
    // The default replay of a fresh checkout must exist
    #[test]
    fn synthetic_replay_is_there() {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../assets/replay/replay.synthetic.bin");
        assert!(p.exists());
    }
}

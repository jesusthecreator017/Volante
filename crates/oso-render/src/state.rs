use std::sync::Arc;
use winit::{
    event::{MouseButton, MouseScrollDelta},
    event_loop::ActiveEventLoop,
    keyboard::KeyCode,
    window::Window,
};

use crate::assets::load_bytes;
use crate::calib::{self, Calib};
use crate::playback::{Command, Playback};
use crate::renderer::Renderer;
use crate::renderer::gpu::Gpu;
use crate::renderer::lines::LineVertex;
use crate::renderer::model::Model;
use crate::replay::{self, BONES, JOINTS, Replay, TrailPoint};
use crate::scene::body::{self, BodyPart};
use crate::scene::camera::{Camera, Projection};
use crate::scene::camera_controller::CameraController;
use crate::scene::components::{
    CameraMarker, ModelHandle, PointLight, Racquet, Shuttle, Transform,
};
use crate::scene::court;
use crate::scene::hall;
use crate::scene::racquet;
use crate::scene::rig::{self, Palette, PlayerRig, Rig};
use crate::scene::shuttle;
use web_time::Instant;

const DEFAULT_FOVY: f32 = 45.0;
// P0 orange, P1 cyan (linear, HDR target); darker when a joint is gap-filled (conf 0.5)
pub const PLAYER_COLORS: [[f32; 3]; 2] = [[1.0, 0.45, 0.05], [0.0, 0.8, 1.0]];
const CROSS_HALF: f32 = 0.03;
const RACQUET_FRAME_COLOR: [f32; 4] = [0.12, 0.12, 0.12, 1.0];
const RACQUET_GRIP_COLOR: [f32; 4] = [0.9, 0.9, 0.9, 1.0];
// Hall colors are sRGB (the 1x1 textures decode to linear): walls ~0.25 linear blue-gray, ceiling ~0.55, gray-brown floor.
const MARKER_COLOR: [f32; 4] = [0.25, 0.25, 0.25, 1.0];
const MARKER_SCALE: f32 = 0.15;
// Net body grid, linear
const NET_LINE_COLOR: [f32; 3] = [0.08, 0.08, 0.08];
const HALL_FLOOR_COLOR: [f32; 4] = [0.42, 0.4, 0.34, 1.0];
const HALL_WALL_COLOR: [f32; 4] = [0.42, 0.52, 0.47, 1.0];
const HALL_CEILING_COLOR: [f32; 4] = [0.77, 0.77, 0.79, 1.0];
// Alpha below 0.9 makes model.wgsl draw the material unlit and bright (HDR); the fixture glows warm white
const HALL_FIXTURE_COLOR: [f32; 4] = [1.0, 0.95, 0.85, 0.5];
const RACQUET_OBJ: &str = "models/racquet/WhiteBadmintonRacket.obj";
const SHUTTLE_COLOR: [f32; 4] = [0.95, 0.95, 0.95, 1.0];
// "Big shuttle": uniform scale so it reads from the far camera
const BIG_SHUTTLE_SCALE: f32 = 3.0;
// `--distractors` (render-only): copies of the players on the neighbouring court, turned 180 deg about the vertical,
// playing the replay this many seconds later
const DISTRACTOR_Z: f32 = 8.1;
const DISTRACTOR_SHIFT_S: f64 = 3.7;

// Ties the scene (ECS world, camera) to the renderer
/// Hooks for the app-specific parts of the scene; every method defaults to doing nothing.
pub trait Extras {
    /// Once, right after the replay parsed (Replay publishes stroke/stat data to the page here)
    fn replay_loaded(&mut self, _r: &Replay) {}
    /// Adds app-specific entities and models while the scene is built (only with a replay)
    fn spawn(
        &mut self,
        _world: &mut hecs::World,
        _models: &mut Vec<Model>,
        _device: &wgpu::Device,
        _queue: &wgpu::Queue,
        _layout: &wgpu::BindGroupLayout,
        _r: &Replay,
    ) -> anyhow::Result<()> {
        Ok(())
    }
    /// Every frame after the bodies moved, at playback time `t`
    fn update(&mut self, _world: &mut hecs::World, _r: &Replay, _t: f64) {}
    /// Which `PLAYER_COLORS` entry each replay slot is drawn in (body, skeleton and racquet) at `t`. Default: the slot's own. Arena
    /// maps slots to models, so a model keeps its colour across a change of ends.
    fn slot_colors(&self, _r: &Replay, _t: f64) -> [usize; 2] {
        [0, 1]
    }
    /// A procedural racquet in the racquet local frame (see `scene::racquet`), flat-colored parts tinted for the player color `color`.
    /// Some: the app never loads the racquet OBJ and the racquet follows `slot_colors`. Default None: the OBJ.
    fn racquet_parts(&self, _color: [f32; 3]) -> Option<Vec<(court::CpuMesh, [f32; 4])>> {
        None
    }
}

pub struct State {
    pub window: Arc<Window>,
    renderer: Renderer,
    world: hecs::World,
    // ModelHandle(i) points at models[i]
    models: Vec<Model>,
    camera: Camera,
    projection: Projection,
    camera_controller: CameraController,
    calib: Option<Calib>,
    replay: Option<Replay>,
    playback: Playback,
    // Commands from keys and (on web) the page, applied in order by update
    commands: Vec<Command>,
    show_skeleton: bool,
    big_shuttle: bool,
    // Render-only (--render-frames): the shuttle trail would be a drawn-on line in a "camera" video
    hide_trail: bool,
    show_markers: bool,
    // Net body grids of every court, built once and appended to the line batch each frame
    net_lines: Vec<LineVertex>,
    // Shuttle heading (kept while it is nearly at rest) and the trail scratch buffer, reused every frame
    shuttle_rot: cgmath::Quaternion<f32>,
    trail: Vec<TrailPoint>,
    // Per player, refreshed every update and reused: interpolated joints and each body slot's pose
    samples: Vec<[[f32; 4]; JOINTS]>,
    // Blob shadow per player
    shadows: Vec<Option<Transform>>,
    // The skinned humanoid; None when it failed to load (players are drawn as skeleton lines then)
    rig: Option<Rig>,
    rigs: Vec<PlayerRig>,
    // Per player, refreshed every update: the joint palette, None while the hips are missing
    palettes: Vec<Option<Palette>>,
    // Per player, refreshed every update: the racquet pose, None when hidden or without a racquet track
    racquets: Vec<Option<Transform>>,
    // First model of the per-color procedural racquets (one per PLAYER_COLORS entry), None with the OBJ or the blocky fallback
    racquet_tints: Option<usize>,
    // Color index of each slot as last pushed to the renderer (Extras::slot_colors)
    slot_colors: [usize; 2],
    // App-specific scene parts and per-frame updates (Replay: stats export; Arena: scoreboard)
    extras: Box<dyn Extras>,
    // Set by seeks and steps: bodies re-derive orientation from scratch instead of from history
    reorient: bool,
    last_tick: Instant,
    // Rebuilt every frame, reused to avoid reallocating
    line_vertices: Vec<LineVertex>,
    // Orbit only while the left button is held
    mouse_pressed: bool,
    right_pressed: bool,
}

impl State {
    pub async fn new(window: Arc<Window>, mut extras: Box<dyn Extras>) -> anyhow::Result<State> {
        let gpu = Gpu::new(window.clone()).await?;

        // Starts at the behind-baseline preset
        let mut camera = Camera::new((0.0, 0.0, 0.0), cgmath::Deg(0.0), cgmath::Deg(30.0), 10.0);
        camera.apply_preset(1);
        let projection = Projection::new(gpu.aspect(), cgmath::Deg(DEFAULT_FOVY), 0.05, 200.0);

        let mut renderer = Renderer::new(gpu, &camera, &projection)?;
        let (device, queue, layout) = (
            &renderer.gpu.device,
            &renderer.gpu.queue,
            &renderer.material_layout,
        );
        // Model 0 must stay the cube: the calibrated-camera markers below use ModelHandle(0). Flat dark gray.
        let (cube_vertices, cube_indices) = {
            let mut mesh = (Vec::new(), Vec::new());
            racquet::push_box(
                &mut mesh,
                cgmath::Vector3::new(0.0, 0.0, 0.0),
                [
                    cgmath::Vector3::unit_x(),
                    cgmath::Vector3::unit_y(),
                    cgmath::Vector3::unit_z(),
                ],
                [1.0; 3],
            );
            court::finish(mesh)
        };
        let mut models = vec![Model::from_mesh(
            device,
            queue,
            layout,
            "Camera marker",
            &cube_vertices,
            &cube_indices,
            MARKER_COLOR,
        )?];

        let mut world = hecs::World::new();

        let parts = [
            ("Floor", court::floor(), [0.16, 0.45, 0.26, 1.0]),
            ("Lines", court::lines(), [1.0, 1.0, 1.0, 1.0]),
            ("Net tape", court::net(), [0.95, 0.95, 0.95, 1.0]),
            ("Posts", court::posts(), [0.2, 0.2, 0.2, 1.0]),
        ];
        // One model per court part, one instance per court in the hall (ours first, at the origin)
        for (name, (vertices, indices), color) in parts {
            let handle = ModelHandle(models.len());
            for [x, z] in hall::COURT_CENTERS {
                world.spawn((
                    Transform {
                        position: cgmath::Vector3::new(x, 0.0, z),
                        rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                        scale: 1.0,
                    },
                    handle,
                ));
            }
            models.push(Model::from_mesh(
                device, queue, layout, name, &vertices, &indices, color,
            )?);
        }

        // The hall itself: static shell at the origin
        let shell = [
            ("Hall floor", hall::floor(), HALL_FLOOR_COLOR),
            ("Hall walls", hall::walls(), HALL_WALL_COLOR),
            ("Hall ceiling", hall::ceiling(), HALL_CEILING_COLOR),
            ("Hall fixtures", hall::fixtures(), HALL_FIXTURE_COLOR),
        ];
        for (name, (vertices, indices), color) in shell {
            world.spawn((
                Transform {
                    position: cgmath::Vector3::new(0.0, 0.0, 0.0),
                    rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                    scale: 1.0,
                },
                ModelHandle(models.len()),
            ));
            models.push(Model::from_mesh(
                device, queue, layout, name, &vertices, &indices, color,
            )?);
        }
        for position in hall::light_positions() {
            world.spawn((
                Transform {
                    position: position.into(),
                    rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                    scale: 1.0,
                },
                PointLight {
                    color: hall::LIGHT_COLOR,
                    range: hall::LIGHT_RANGE,
                },
            ));
        }

        // Optional: without a calibration file the Phase 0 scene is unchanged
        let calib = match load_bytes("calib/calib.json")
            .await
            .and_then(|b| calib::parse(&b))
        {
            Ok(c) => Some(c),
            Err(e) => {
                log::warn!("no calibration: {e:#}");
                None
            }
        };
        for cam in calib.iter().flat_map(|c| &c.cameras) {
            world.spawn((
                Transform {
                    position: cam.position.into(),
                    rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                    // Hidden until C
                    scale: 0.0,
                },
                ModelHandle(0),
                CameraMarker,
            ));
        }

        // Optional like the calibration: without a replay the scene is unchanged
        let replay = match crate::assets::load_replay()
            .await
            .and_then(|b| Replay::parse(&b))
        {
            Ok(r) => Some(r),
            Err(e) => {
                log::warn!("no replay: {e:#}");
                #[cfg(target_arch = "wasm32")]
                crate::web_api::set_replay_error(format!("{e:#}"));
                None
            }
        };

        let mut racquet_tints = None;
        if let Some(r) = &replay {
            extras.replay_loaded(r);
        }
        if let Some(r) = &replay {
            spawn_shadows(&mut world, &mut models, r.players, device, queue, layout)?;
            racquet_tints = spawn_racquets(
                &mut world,
                &mut models,
                r.players,
                device,
                queue,
                layout,
                extras.as_ref(),
            )
            .await?;
            extras.spawn(&mut world, &mut models, device, queue, layout, r)?;
            if r.has_shuttle() {
                let (vertices, indices) = shuttle::mesh();
                world.spawn((
                    Transform {
                        position: cgmath::Vector3::new(0.0, 0.0, 0.0),
                        rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                        scale: 0.0,
                    },
                    ModelHandle(models.len()),
                    Shuttle,
                ));
                models.push(Model::from_mesh(
                    device,
                    queue,
                    layout,
                    "Shuttle",
                    &vertices,
                    &indices,
                    SHUTTLE_COLOR,
                )?);
            }
        }

        // Optional: without the rig players fall back to skeleton lines
        let players = replay.as_ref().map_or(0, |r| r.players);
        let rig = match load_rig().await {
            Ok(rig) if players > 0 => {
                let colors: Vec<[f32; 3]> = (0..players)
                    .map(|p| PLAYER_COLORS[p % PLAYER_COLORS.len()])
                    .collect();
                renderer.set_rig(&rig, &colors);
                Some(rig)
            }
            Ok(_) => None,
            Err(e) => {
                log::warn!("no humanoid rig, drawing skeleton lines: {e:#}");
                None
            }
        };
        let rigs = match (&rig, &replay) {
            (Some(rig), Some(r)) => (0..players)
                .map(|p| PlayerRig::new(rig, &body::Dims::measure(r, p)))
                .collect(),
            _ => Vec::new(),
        };
        let duration = replay
            .as_ref()
            .map_or(0.0, |r| r.frame_count as f64 / r.fps as f64);
        Ok(Self {
            window,
            renderer,
            world,
            models,
            camera,
            projection,
            camera_controller: CameraController::new(0.004),
            calib,
            replay,
            playback: Playback::new(duration),
            commands: Vec::new(),
            show_skeleton: rig.is_none() && players > 0,
            big_shuttle: false,
            hide_trail: false,
            show_markers: false,
            net_lines: net_line_vertices(),
            shuttle_rot: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
            trail: Vec::new(),
            samples: vec![[[0.0; 4]; JOINTS]; players],
            shadows: vec![None; players],
            rig,
            rigs,
            palettes: vec![None; players],
            racquets: vec![None; players],
            racquet_tints,
            slot_colors: [0, 1],
            extras,
            reorient: false,
            last_tick: Instant::now(),
            line_vertices: Vec::new(),
            mouse_pressed: false,
            right_pressed: false,
        })
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        self.renderer.resize(width, height);
        self.projection.resize(self.renderer.gpu.aspect());
    }

    pub fn render(&mut self) -> anyhow::Result<()> {
        self.window.request_redraw();
        self.build_lines();
        self.renderer.render(
            &self.world,
            &self.models,
            &self.camera,
            &self.projection,
            &self.line_vertices,
            &self.palettes,
        )
    }

    // `cargo run --release -- --screenshot out.png ...`: seek, pause, set the camera, render one offscreen frame to a PNG
    #[cfg(not(target_arch = "wasm32"))]
    pub fn screenshot(&mut self, shot: &crate::app::Shot) -> anyhow::Result<()> {
        let (w, h) = shot.size;
        self.resize(w, h);
        if let Some(job) = &shot.frames {
            return self.render_frames(shot, job);
        }
        // Same path as the keys and web buttons, so seeking warms up the body orientation
        self.commands.extend([
            Command::Seek(shot.time),
            Command::Pause,
            Command::Preset(shot.preset),
        ]);
        self.update();
        if let (Some(eye), Some(target)) = (shot.eye, shot.target) {
            self.camera = Camera::look_from(eye, target);
            self.projection.set_fovy(cgmath::Deg(DEFAULT_FOVY));
        }
        self.build_lines();
        let rgba = self.renderer.render_to_rgba(
            &self.world,
            &self.models,
            &self.camera,
            &self.projection,
            &self.line_vertices,
            &self.palettes,
        )?;
        image::save_buffer(&shot.path, &rgba, w, h, image::ColorType::Rgba8)?;
        log::info!("wrote {}", shot.path.display());
        Ok(())
    }

    // `--render-frames`: the calibrated camera `job.camera`, one PNG per frame (000000.png ...) in `shot.path`.
    // One process, one renderer; PNG encoding runs on a worker thread so the GPU is not left waiting.
    #[cfg(not(target_arch = "wasm32"))]
    fn render_frames(
        &mut self,
        shot: &crate::app::Shot,
        job: &crate::app::FrameJob,
    ) -> anyhow::Result<()> {
        let (w, h) = shot.size;
        let index = self
            .calib
            .as_ref()
            .and_then(|c| c.cameras.iter().position(|cam| cam.id == job.camera))
            .ok_or_else(|| anyhow::anyhow!("no calibrated camera '{}'", job.camera))?;
        anyhow::ensure!(self.replay.is_some(), "--render-frames needs a replay");
        // Same pixel scale as the calibrated lens at any --size: a larger size widens the view (margin for lens distortion)
        let fovy = self.calib.as_ref().map_or(0.0, |c| {
            2.0 * (h as f32 / (2.0 * c.cameras[index].k[1][1])).atan()
        });
        std::fs::create_dir_all(&shot.path)?;
        self.hide_trail = true;
        if job.distractors {
            self.add_distractors();
        }
        let n = ((job.t1 - job.t0) * job.fps).round() as usize;
        let (tx, rx) = std::sync::mpsc::sync_channel::<(usize, Vec<u8>)>(8);
        let dir = shot.path.clone();
        let writer = std::thread::spawn(move || -> anyhow::Result<()> {
            use image::codecs::png::{CompressionType, FilterType, PngEncoder};
            for (i, rgba) in rx {
                let file = std::fs::File::create(dir.join(format!("{i:06}.png")))?;
                PngEncoder::new_with_quality(
                    std::io::BufWriter::new(file),
                    CompressionType::Fast,
                    FilterType::Sub,
                )
                .encode(&rgba, w, h, image::ColorType::Rgba8)?;
            }
            Ok(())
        });
        let start = Instant::now();
        let mut result = Ok(());
        for i in 0..n {
            // Seek re-derives the body orientation from the 30 frames before, so every frame is independent of the loop
            let t = job.offset + job.t0 + i as f64 / (job.fps * job.rate);
            self.commands.extend([
                Command::Seek(t),
                Command::Pause,
                Command::Preset(4 + index as u8),
            ]);
            self.update();
            self.projection.set_fovy(cgmath::Rad(fovy));
            self.build_lines();
            let rgba = self.renderer.render_to_rgba(
                &self.world,
                &self.models,
                &self.camera,
                &self.projection,
                &self.line_vertices,
                &self.palettes,
            );
            match rgba {
                // A closed channel means the writer failed; its error is reported below
                Ok(rgba) => {
                    if tx.send((i, rgba)).is_err() {
                        break;
                    }
                }
                Err(e) => {
                    result = Err(e);
                    break;
                }
            }
        }
        drop(tx);
        let written = writer
            .join()
            .map_err(|_| anyhow::anyhow!("PNG writer panicked"))?;
        result?;
        written?;
        let secs = start.elapsed().as_secs_f64();
        println!(
            "rendered {n} frames in {secs:.1} s = {:.1} frames/s",
            n as f64 / secs
        );
        Ok(())
    }

    // Render-only: two extra rigged players on the neighbouring court (slots after the replay's players)
    #[cfg(not(target_arch = "wasm32"))]
    fn add_distractors(&mut self) {
        let (Some(r), Some(rig)) = (&self.replay, &self.rig) else {
            log::warn!("--distractors needs a replay and the humanoid rig");
            return;
        };
        let real = r.players;
        let total = 2 * real;
        let colors: Vec<[f32; 3]> = (0..total)
            .map(|p| PLAYER_COLORS[p % PLAYER_COLORS.len()])
            .collect();
        self.renderer.set_rig(rig, &colors);
        // Shadows reuse the shadow model of player 0
        let shadow = self
            .world
            .query::<(&ModelHandle, &BodyPart)>()
            .iter()
            .next()
            .map(|(h, _)| *h);
        for p in real..total {
            if let Some(handle) = shadow {
                self.world.spawn((
                    Transform {
                        position: cgmath::Vector3::new(0.0, 0.0, 0.0),
                        rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                        scale: 0.0,
                    },
                    handle,
                    BodyPart { player: p },
                ));
            }
            self.rigs
                .push(PlayerRig::new(rig, &body::Dims::measure(r, p - real)));
        }
        self.samples.resize(total, [[0.0; 4]; JOINTS]);
        self.shadows.resize(total, None);
        self.palettes.resize(total, None);
    }

    fn build_lines(&mut self) {
        self.line_vertices.clear();
        self.line_vertices.extend_from_slice(&self.net_lines);
        if self.show_skeleton {
            skeleton_lines(&self.samples, self.slot_colors, &mut self.line_vertices);
        }
        if let Some(r) = self
            .replay
            .as_ref()
            .filter(|r| r.has_shuttle() && !self.hide_trail)
        {
            r.shuttle_trail(self.playback.time, shuttle::TRAIL_WINDOW, &mut self.trail);
            shuttle::trail_lines(&self.trail, &mut self.line_vertices);
        }
    }

    pub fn update(&mut self) {
        let now = Instant::now();
        // Clamp so a stall (window drag, tab in background) does not skip seconds of replay
        let dt = (now - self.last_tick).as_secs_f64().min(0.1);
        self.last_tick = now;
        #[cfg(target_arch = "wasm32")]
        crate::web_api::drain_into(&mut self.commands);
        // Take the buffer so commands can borrow self; it goes back empty with its capacity
        let mut commands = std::mem::take(&mut self.commands);
        for cmd in commands.drain(..) {
            self.apply(cmd);
        }
        self.commands = commands;
        self.playback.advance(dt);
        self.camera_controller
            .update_camera(&mut self.camera, dt as f32);
        self.update_bodies();
        #[cfg(target_arch = "wasm32")]
        self.publish_status();
    }

    fn apply(&mut self, cmd: Command) {
        let Some(fps) = self.replay.as_ref().map(|r| r.fps) else {
            return;
        };
        self.reorient |= matches!(
            cmd,
            Command::Seek(_) | Command::Step(_) | Command::NextStroke | Command::PrevStroke
        );
        let before = self.playback.speed;
        if self.playback.apply(cmd, fps) {
            if self.playback.speed != before {
                log::info!("playback speed {}x", self.playback.speed);
            }
            return;
        }
        match cmd {
            Command::Preset(n) => self.apply_preset(n),
            // Without the rig the skeleton is the only body, so it stays on
            Command::ToggleSkeleton if self.rig.is_some() => {
                self.show_skeleton = !self.show_skeleton
            }
            Command::ToggleBigShuttle => self.big_shuttle = !self.big_shuttle,
            Command::NextStroke | Command::PrevStroke => {
                self.jump_stroke(cmd == Command::NextStroke)
            }
            _ => {}
        }
    }

    // N / P and the web buttons: land just before the stroke's contact, paused
    fn jump_stroke(&mut self, next: bool) {
        let Some(r) = &self.replay else { return };
        let t = self.playback.time;
        let Some(target) = (if next {
            r.next_stroke(t)
        } else {
            r.prev_stroke(t)
        }) else {
            return;
        };
        self.playback.seek(target);
        self.playback.playing = false;
        if let Some(row) = r
            .strokes()
            .iter()
            .find(|row| (Replay::stroke_seek_time(row) - target).abs() < 1e-9)
        {
            log::info!("{}", replay::stroke_summary(row));
        }
    }

    // 1-3: orbit presets with the default lens; 4-5: calibrated camera views
    fn apply_preset(&mut self, preset: u8) {
        if (1..=3).contains(&preset) {
            self.camera.apply_preset(preset);
            self.projection.set_fovy(cgmath::Deg(DEFAULT_FOVY));
        } else if let Some(cam) = self
            .calib
            .as_ref()
            .and_then(|c| c.cameras.get(preset as usize - 4))
        {
            self.camera = Camera::look_from(cam.position, cam.look_target());
            // Vertical FOV matches the framing; a different viewport aspect crops or extends horizontally
            self.projection.set_fovy(cgmath::Rad(cam.fovy()));
            let aspect = self.renderer.gpu.aspect();
            if (aspect - cam.aspect()).abs() > 0.05 {
                log::info!(
                    "viewport aspect {aspect:.2} != camera {} image aspect {:.2}: horizontal framing differs",
                    cam.id,
                    cam.aspect()
                );
            }
        }
    }

    // Samples the replay at the playback time and moves every body part and shadow to match
    fn update_bodies(&mut self) {
        let Some(r) = &self.replay else { return };
        let colors = self.extras.slot_colors(r, self.playback.time);
        if colors != self.slot_colors {
            self.slot_colors = colors;
            let rgb = |p: usize| PLAYER_COLORS[colors[p % 2] % PLAYER_COLORS.len()];
            self.renderer
                .set_player_colors(&(0..self.samples.len()).map(rgb).collect::<Vec<_>>());
        }
        let reorient = std::mem::take(&mut self.reorient);
        for (player, (joints, shadow)) in self.samples.iter_mut().zip(&mut self.shadows).enumerate()
        {
            // Slots past the replay's players are render-only distractors
            let distractor = player >= r.players;
            let (source, time) = if distractor {
                let t = self.playback.time + DISTRACTOR_SHIFT_S;
                (
                    player - r.players,
                    replay::wrap_time(t, r.fps, r.frame_count),
                )
            } else {
                (player, self.playback.time)
            };
            *joints = body::sample_held(r, time, source);
            if distractor {
                turn_to_neighbour_court(joints);
            }
            *shadow = body::shadow_transform(joints);
            let Some(rig) = &self.rig else { continue };
            let pose = &mut self.rigs[player];
            if reorient {
                let frame = replay::frame_at(time, r.fps, r.frame_count);
                if distractor {
                    pose.orient.reset();
                    for f in frame.saturating_sub(30)..frame {
                        let mut j = *r.joints(f, source);
                        turn_to_neighbour_court(&mut j);
                        body::part_transforms(&j, &mut pose.orient);
                    }
                } else {
                    body::warm_up(r, player, frame, &mut pose.orient);
                }
            }
            let palette = self.palettes[player].get_or_insert_with(rig::identity_palette);
            if !rig.pose(joints, pose, palette) {
                self.palettes[player] = None;
            }
        }
        for (t, part) in self.world.query_mut::<(&mut Transform, &BodyPart)>() {
            // Hidden shadows shrink to nothing: the instance stays, so extract_instances needs no special case
            *t = self.shadows[part.player].unwrap_or(Transform { scale: 0.0, ..*t });
        }
        if r.has_shuttle() {
            let scale = if self.big_shuttle {
                BIG_SHUTTLE_SCALE
            } else {
                1.0
            };
            let sample = r.sample_shuttle(self.playback.time);
            if let Some((_, vel, _)) = sample {
                self.shuttle_rot = shuttle::orientation(self.shuttle_rot, vel);
            }
            for (t, _) in self.world.query_mut::<(&mut Transform, &Shuttle)>() {
                *t = match sample {
                    Some((position, _, _)) => Transform {
                        position,
                        rotation: self.shuttle_rot,
                        scale,
                    },
                    None => Transform { scale: 0.0, ..*t },
                };
            }
        }
        for (player, pose) in self.racquets.iter_mut().enumerate() {
            // Rigid and uniform scale 1: the rotation matrix doubles as the normal matrix
            *pose = racquet::pose_for(r, self.playback.time, player, &self.samples[player]).map(
                |(position, rotation)| Transform {
                    position,
                    rotation,
                    scale: 1.0,
                },
            );
        }
        for (t, rq, model) in self
            .world
            .query_mut::<(&mut Transform, &Racquet, &mut ModelHandle)>()
        {
            *t = self.racquets[rq.player].unwrap_or(Transform { scale: 0.0, ..*t });
            if let Some(first) = self.racquet_tints {
                model.0 = first + self.slot_colors[rq.player % 2];
            }
        }
        self.extras.update(&mut self.world, r, self.playback.time);
    }

    #[cfg(target_arch = "wasm32")]
    fn publish_status(&self) {
        let Some(r) = &self.replay else { return };
        crate::web_api::set_status(crate::web_api::Status {
            loaded: true,
            time: self.playback.time,
            duration: self.playback.duration,
            playing: self.playback.playing,
            speed: self.playback.speed,
            fps: r.fps,
            frame: crate::replay::frame_at(self.playback.time, r.fps, r.frame_count) as u32,
            frame_count: r.frame_count as u32,
            skeleton: self.show_skeleton,
            big_shuttle: self.big_shuttle,
            has_shuttle: self.replay.as_ref().is_some_and(|r| r.has_shuttle()),
            stroke_count: r.strokes().len() as u32,
            cameras: self.calib.as_ref().map_or(0, |c| c.cameras.len() as u32),
        });
    }

    pub fn handle_key(
        &mut self,
        event_loop: &ActiveEventLoop,
        code: KeyCode,
        is_pressed: bool,
        repeat: bool,
    ) {
        if self.camera_controller.handle_key(code, is_pressed) && is_pressed {
            // Presets 1-3 are the Phase 0 views with the default lens
            self.projection.set_fovy(cgmath::Deg(DEFAULT_FOVY));
        }
        if is_pressed && !repeat && code == KeyCode::KeyC {
            self.show_markers = !self.show_markers;
            let scale = if self.show_markers { MARKER_SCALE } else { 0.0 };
            for (t, _) in self.world.query_mut::<(&mut Transform, &CameraMarker)>() {
                t.scale = scale;
            }
        }
        if is_pressed {
            self.handle_playback_key(code, repeat);
        }
        match code {
            KeyCode::Digit4 if is_pressed => self.apply_preset(4),
            KeyCode::Digit5 if is_pressed => self.apply_preset(5),
            _ => {}
        }
        if code == KeyCode::Escape && is_pressed {
            event_loop.exit();
        }
    }

    // Keys only queue Commands; update applies them, same as the web buttons
    fn handle_playback_key(&mut self, code: KeyCode, repeat: bool) {
        let cmd = match code {
            // Held Space must not flip play/pause every repeat; held , and . keep stepping
            // (arrows fly the camera)
            KeyCode::Space if !repeat => Command::TogglePlay,
            KeyCode::Period | KeyCode::Comma => match step_command(code) {
                Some(c) => c,
                None => return,
            },
            KeyCode::BracketLeft => Command::SetSpeed(self.playback.speed_step(-1)),
            KeyCode::BracketRight => Command::SetSpeed(self.playback.speed_step(1)),
            KeyCode::Home => Command::Seek(0.0),
            KeyCode::KeyK if !repeat => Command::ToggleSkeleton,
            KeyCode::KeyV if !repeat => Command::ToggleBigShuttle,
            // Held keys keep stepping through strokes
            KeyCode::KeyN => Command::NextStroke,
            KeyCode::KeyP => Command::PrevStroke,
            _ => return,
        };
        self.commands.push(cmd);
    }

    pub fn handle_mouse_button(&mut self, button: MouseButton, pressed: bool) {
        match button {
            MouseButton::Left => self.mouse_pressed = pressed,
            MouseButton::Right => self.right_pressed = pressed,
            _ => {}
        }
    }

    pub fn handle_focus_lost(&mut self) {
        self.camera_controller.clear_keys();
        self.mouse_pressed = false;
        self.right_pressed = false;
    }

    pub fn handle_mouse_motion(&mut self, dx: f64, dy: f64) {
        if self.right_pressed {
            self.camera_controller.handle_mouse(dx, dy, true);
        } else if self.mouse_pressed {
            self.camera_controller.handle_mouse(dx, dy, false);
        }
    }

    pub fn handle_scroll(&mut self, delta: &MouseScrollDelta) {
        self.camera_controller.handle_scroll(delta);
    }
}

// 180 deg about the vertical through the court center, then onto the neighbouring court
fn turn_to_neighbour_court(joints: &mut [[f32; 4]; JOINTS]) {
    for j in joints.iter_mut().filter(|j| j[3] > 0.0) {
        *j = [-j[0], j[1], DISTRACTOR_Z - j[2], j[3]];
    }
}

// The see-through net bodies of all hall courts as LineList pairs
fn net_line_vertices() -> Vec<LineVertex> {
    let segments = court::net_lines();
    hall::COURT_CENTERS
        .iter()
        .flat_map(|&[cx, cz]| {
            segments.iter().flat_map(move |(a, b)| {
                [a, b].map(|p| LineVertex {
                    position: [p[0] + cx, p[1], p[2] + cz],
                    color: NET_LINE_COLOR,
                })
            })
        })
        .collect()
}

// Frame-step keys: , and . (arrows fly the camera)
fn step_command(code: KeyCode) -> Option<Command> {
    match code {
        KeyCode::Period => Some(Command::Step(1)),
        KeyCode::Comma => Some(Command::Step(-1)),
        _ => None,
    }
}

// Spawns each player's blob shadow, hidden (scale 0) until the first update.
fn spawn_shadows(
    world: &mut hecs::World,
    models: &mut Vec<Model>,
    players: usize,
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
) -> anyhow::Result<()> {
    let hidden = Transform {
        position: cgmath::Vector3::new(0.0, 0.0, 0.0),
        rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
        scale: 0.0,
    };
    let (vertices, indices) = body::shadow_mesh();
    let shadow = models.len();
    models.push(Model::from_mesh(
        device,
        queue,
        layout,
        "Shadow",
        &vertices,
        &indices,
        [0.02, 0.02, 0.02, 1.0],
    )?);
    for player in 0..players {
        world.spawn((hidden, ModelHandle(shadow), BodyPart { player }));
    }
    Ok(())
}

// The glTF and its one buffer, from assets/models/humanoid
async fn load_rig() -> anyhow::Result<Rig> {
    let gltf = load_bytes("models/humanoid/scene.gltf").await?;
    let bin = load_bytes("models/humanoid/scene.bin").await?;
    Rig::parse(&gltf, &bin)
}

// Spawns one racquet per player, hidden until the first update: the app's procedural racquet (Extras::racquet_parts, one model per
// player color; returns the first model's index), else the OBJ model (one draw per material), or the blocky two-mesh fallback if it
// fails to load. Players without an IMU get the arm estimate (racquet::estimate_from_arm).
pub(crate) async fn spawn_racquets(
    world: &mut hecs::World,
    models: &mut Vec<Model>,
    players: usize,
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
    extras: &dyn Extras,
) -> anyhow::Result<Option<usize>> {
    let hidden = Transform {
        position: cgmath::Vector3::new(0.0, 0.0, 0.0),
        rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
        scale: 0.0,
    };
    let first = models.len();
    let tinted: Option<Vec<_>> = PLAYER_COLORS
        .iter()
        .map(|&c| extras.racquet_parts(c))
        .collect();
    if let Some(variants) = tinted {
        for parts in &variants {
            let refs: Vec<_> = parts
                .iter()
                .map(|((v, i), color)| (v.as_slice(), i.as_slice(), *color))
                .collect();
            models.push(Model::from_parts(device, queue, layout, "Racquet", &refs)?);
        }
        // Which of the variants a player holds is set every update (update_bodies); player p starts with color p
        for player in 0..players {
            world.spawn((
                hidden,
                ModelHandle(first + player % PLAYER_COLORS.len()),
                Racquet { player },
            ));
        }
        return Ok(Some(first));
    }
    match load_racquet(device, queue, layout).await {
        Ok(model) => models.push(model),
        Err(e) => {
            log::warn!("no racquet model, using the blocky one: {e:#}");
            let [frame, grip] = racquet::meshes();
            for (name, (vertices, indices), color) in [
                ("Racquet frame", frame, RACQUET_FRAME_COLOR),
                ("Racquet grip", grip, RACQUET_GRIP_COLOR),
            ] {
                models.push(Model::from_mesh(
                    device, queue, layout, name, &vertices, &indices, color,
                )?);
            }
        }
    }
    for player in 0..players {
        for model in first..models.len() {
            world.spawn((hidden, ModelHandle(model), Racquet { player }));
        }
    }
    Ok(None)
}

async fn load_racquet(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
) -> anyhow::Result<Model> {
    let parts = racquet::from_obj(&load_bytes(RACQUET_OBJ).await?)?;
    let refs: Vec<_> = parts
        .iter()
        .map(|((v, i), color)| (v.as_slice(), i.as_slice(), *color))
        .collect();
    Model::from_parts(device, queue, layout, "Racquet", &refs)
}

// Both players' bones plus a small cross at each joint (colored by `slot_colors`, see Extras::slot_colors), appended to `out` as LineList pairs.
// Joints with conf 0 are skipped (bones need both ends), conf 0.5 (gap-filled) is drawn darker.
fn skeleton_lines(
    players: &[[[f32; 4]; JOINTS]],
    slot_colors: [usize; 2],
    out: &mut Vec<LineVertex>,
) {
    for (player, joints) in players.iter().enumerate() {
        let base = PLAYER_COLORS[slot_colors[player % 2] % PLAYER_COLORS.len()];
        let color = |conf: f32| base.map(|c| c * if conf >= 1.0 { 1.0 } else { 0.4 });
        let mut line = |a: [f32; 3], b: [f32; 3], conf: f32| {
            let color = color(conf);
            out.push(LineVertex { position: a, color });
            out.push(LineVertex { position: b, color });
        };
        for &(a, b) in &BONES {
            let (ja, jb) = (joints[a], joints[b]);
            if ja[3] > 0.0 && jb[3] > 0.0 {
                line(
                    [ja[0], ja[1], ja[2]],
                    [jb[0], jb[1], jb[2]],
                    ja[3].min(jb[3]),
                );
            }
        }
        for j in joints.iter().filter(|j| j[3] > 0.0) {
            for axis in 0..3 {
                let (mut a, mut b) = ([j[0], j[1], j[2]], [j[0], j[1], j[2]]);
                a[axis] -= CROSS_HALF;
                b[axis] += CROSS_HALF;
                line(a, b, j[3]);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &[u8] = include_bytes!("../../../assets/replay/replay.bin");

    fn sample_all(r: &Replay, t: f64) -> Vec<[[f32; 4]; JOINTS]> {
        (0..r.players).map(|p| r.sample(t, p)).collect()
    }

    #[test]
    fn step_keys_are_comma_period_not_arrows() {
        assert!(matches!(
            step_command(KeyCode::Comma),
            Some(Command::Step(-1))
        ));
        assert!(matches!(
            step_command(KeyCode::Period),
            Some(Command::Step(1))
        ));
        assert!(step_command(KeyCode::ArrowLeft).is_none());
        assert!(step_command(KeyCode::ArrowRight).is_none());
    }

    #[test]
    fn skeleton_lines_counts_and_colors() {
        let r = Replay::parse(SAMPLE).unwrap();
        let mut v = Vec::new();
        skeleton_lines(&sample_all(&r, 0.0), [0, 1], &mut v);
        // At most (16 bones + 17 crosses * 3) lines per player, 2 vertices each; every line is a pair
        assert!(v.len() % 2 == 0 && v.len() <= 2 * 2 * (16 + 51) && v.len() > 2 * 2 * 40);
        assert_eq!(v[0].color, PLAYER_COLORS[0]);
    }

    #[test]
    fn skeleton_colors_follow_the_slot_to_color_mapping() {
        let r = Replay::parse(SAMPLE).unwrap();
        let (mut keep, mut swap) = (Vec::new(), Vec::new());
        skeleton_lines(&sample_all(&r, 0.0), [0, 1], &mut keep);
        skeleton_lines(&sample_all(&r, 0.0), [1, 0], &mut swap);
        assert_eq!(keep.len(), swap.len());
        // Same geometry; each slot's lines take the other player color
        let first_p1 = keep
            .iter()
            .position(|v| v.color == PLAYER_COLORS[1])
            .unwrap();
        assert!(
            keep.iter()
                .zip(&swap)
                .all(|(a, b)| a.position == b.position)
        );
        assert_eq!(swap[0].color, PLAYER_COLORS[1]);
        assert_eq!(swap[first_p1].color, PLAYER_COLORS[0]);
    }

    #[test]
    fn distractor_is_a_half_turn_onto_the_next_court() {
        let r = Replay::parse(SAMPLE).unwrap();
        let mut j = r.sample(1.0, 0);
        let before = j;
        turn_to_neighbour_court(&mut j);
        for (a, b) in before.iter().zip(&j) {
            if a[3] > 0.0 {
                assert_eq!([b[0], b[1], b[2]], [-a[0], a[1], DISTRACTOR_Z - a[2]]);
            }
            assert_eq!(a[3], b[3]);
        }
        // Distances between joints are unchanged (a proper rotation, not a mirror)
        let d = |j: &[[f32; 4]; JOINTS], a: usize, b: usize| {
            (0..3)
                .map(|k| (j[a][k] - j[b][k]).powi(2))
                .sum::<f32>()
                .sqrt()
        };
        assert!((d(&before, 5, 9) - d(&j, 5, 9)).abs() < 1e-5);
    }

    // Per-frame cost: run with `cargo test --release bench_skeleton_lines -- --nocapture`
    #[test]
    fn bench_skeleton_lines() {
        let r = Replay::parse(SAMPLE).unwrap();
        let mut v = Vec::new();
        let start = Instant::now();
        for i in 0..1000 {
            v.clear();
            skeleton_lines(&sample_all(&r, i as f64 / 60.0), [0, 1], &mut v);
        }
        println!("skeleton_lines: {:?} per frame", start.elapsed() / 1000);
    }
}

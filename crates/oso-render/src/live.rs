//! Native-only "live" scene: one racquet, a light and a floor grid in a fixed-camera window, driven by a
//! [`LiveSource`] (`oso-rec live`). Reuses the shared renderer and the racquet model of the Replay app.

use std::sync::Arc;
use winit::{
    application::ApplicationHandler,
    event::{ElementState, KeyEvent, WindowEvent},
    event_loop::{ActiveEventLoop, EventLoop},
    keyboard::{KeyCode, PhysicalKey},
    window::{Window, WindowId},
};

use crate::renderer::Renderer;
use crate::renderer::gpu::Gpu;
use crate::renderer::lines::LineVertex;
use crate::renderer::model::Model;
use crate::scene::camera::{Camera, Projection};
use crate::scene::components::{PointLight, Racquet, Transform};
use crate::scene::racquet::LENGTH;

/// Racquet mid-point is the pivot: the model is shifted so it turns about its centre
const PIVOT: f32 = LENGTH / 2.0;
const GRID_HALF: f32 = 1.5;
const GRID_STEP: f32 = 0.25;
const GRID_Y: f32 = -0.42;
const GRID_COLOR: [f32; 3] = [0.08, 0.1, 0.13];
/// The +X axis of the floor: where "forward" (after re-centring) points
const FORWARD_COLOR: [f32; 3] = [0.35, 0.12, 0.05];

pub enum LiveKey {
    Recenter,
    Marker,
    /// C: next step of the mount calibration
    Calibrate,
    /// Esc or the window's close button; the window closes right after
    Quit,
}

/// What to draw this frame
pub struct LiveFrame {
    /// Racquet orientation, `[x, y, z, w]`, local frame (+Y toward the tip) -> court frame (+Y up)
    pub rotation: [f64; 4],
    /// The readout (window title); Some only when it changed since the last frame
    pub title: Option<String>,
    /// Close the window (the source has its own reason, e.g. `--secs`)
    pub quit: bool,
}

pub trait LiveSource {
    /// Once per frame
    fn frame(&mut self) -> LiveFrame;
    fn key(&mut self, key: LiveKey);
}

/// `--screenshot`: one hidden-window frame to a PNG
pub struct LiveShot {
    pub path: std::path::PathBuf,
    pub size: (u32, u32),
}

/// Opens the window and runs until it closes (or renders the one screenshot frame)
pub fn run_live(source: Box<dyn LiveSource>, shot: Option<LiveShot>) -> anyhow::Result<()> {
    let _ = env_logger::try_init();
    let mut app = LiveApp {
        source,
        shot,
        scene: None,
        error: None,
    };
    EventLoop::new()?.run_app(&mut app)?;
    app.error.map_or(Ok(()), Err)
}

struct Scene {
    window: Arc<Window>,
    renderer: Renderer,
    world: hecs::World,
    models: Vec<Model>,
    camera: Camera,
    projection: Projection,
    lines: Vec<LineVertex>,
}

impl Scene {
    async fn new(window: Arc<Window>) -> anyhow::Result<Self> {
        let gpu = Gpu::new(window.clone()).await?;
        let camera = Camera::look_from([0.0, 0.85, 0.95], [0.0, 0.0, 0.0]);
        let projection = Projection::new(gpu.aspect(), cgmath::Deg(45.0), 0.05, 50.0);
        let renderer = Renderer::new(gpu, &camera, &projection)?;
        let mut world = hecs::World::new();
        let mut models = Vec::new();
        crate::state::spawn_racquets(
            &mut world,
            &mut models,
            1,
            &renderer.gpu.device,
            &renderer.gpu.queue,
            &renderer.material_layout,
        )
        .await?;
        // Key light from the camera side, a weaker fill from behind so a racquet turned away does not vanish
        for (position, color) in [
            ([0.8, 1.6, 1.4], [14.0, 13.0, 11.5]),
            ([-0.9, 0.6, -1.2], [5.0, 5.5, 7.0]),
        ] {
            world.spawn((
                Transform {
                    position: position.into(),
                    rotation: cgmath::Quaternion::new(1.0, 0.0, 0.0, 0.0),
                    scale: 1.0,
                },
                PointLight { color, range: 12.0 },
            ));
        }
        Ok(Self {
            window,
            renderer,
            world,
            models,
            camera,
            projection,
            lines: grid(),
        })
    }

    fn pose(&mut self, rotation: [f64; 4]) {
        let [x, y, z, w] = rotation.map(|c| c as f32);
        let q = cgmath::Quaternion::new(w, x, y, z);
        let pivot = cgmath::Rotation::rotate_vector(&q, cgmath::Vector3::new(0.0, PIVOT, 0.0));
        for (t, _) in self.world.query_mut::<(&mut Transform, &Racquet)>() {
            *t = Transform {
                position: -pivot,
                rotation: q,
                scale: 1.0,
            };
        }
    }

    fn render(&mut self) -> anyhow::Result<()> {
        self.renderer.render(
            &self.world,
            &self.models,
            &self.camera,
            &self.projection,
            &self.lines,
            &[],
        )
    }

    fn resize(&mut self, width: u32, height: u32) {
        self.renderer.resize(width, height);
        self.projection.resize(self.renderer.gpu.aspect());
    }

    fn save(&mut self, shot: &LiveShot) -> anyhow::Result<()> {
        let (w, h) = shot.size;
        self.resize(w, h);
        let rgba = self.renderer.render_to_rgba(
            &self.world,
            &self.models,
            &self.camera,
            &self.projection,
            &self.lines,
            &[],
        )?;
        image::save_buffer(&shot.path, &rgba, w, h, image::ColorType::Rgba8)?;
        log::info!("wrote {}", shot.path.display());
        Ok(())
    }
}

/// Floor grid as LineList pairs, with the +X ("forward") line highlighted
fn grid() -> Vec<LineVertex> {
    let mut v = Vec::new();
    let n = (GRID_HALF / GRID_STEP) as i32;
    let mut line = |a: [f32; 3], b: [f32; 3], color: [f32; 3]| {
        v.push(LineVertex { position: a, color });
        v.push(LineVertex { position: b, color });
    };
    for i in -n..=n {
        let c = i as f32 * GRID_STEP;
        line([c, GRID_Y, -GRID_HALF], [c, GRID_Y, GRID_HALF], GRID_COLOR);
        let color = if i == 0 { FORWARD_COLOR } else { GRID_COLOR };
        let (a, b) = if i == 0 {
            (0.0, GRID_HALF)
        } else {
            (-GRID_HALF, GRID_HALF)
        };
        line([a, GRID_Y, c], [b, GRID_Y, c], color);
    }
    v
}

struct LiveApp {
    source: Box<dyn LiveSource>,
    shot: Option<LiveShot>,
    scene: Option<Scene>,
    error: Option<anyhow::Error>,
}

impl ApplicationHandler for LiveApp {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.scene.is_some() {
            return;
        }
        let mut attrs = Window::default_attributes()
            .with_title("oso live")
            .with_inner_size(winit::dpi::PhysicalSize::new(900, 700));
        if let Some(shot) = &self.shot {
            attrs = attrs
                .with_visible(false)
                .with_inner_size(winit::dpi::PhysicalSize::new(shot.size.0, shot.size.1));
        }
        let result = event_loop
            .create_window(attrs)
            .map_err(anyhow::Error::from)
            .and_then(|w| pollster::block_on(Scene::new(Arc::new(w))));
        match result {
            Ok(mut scene) => {
                if let Some(shot) = &self.shot {
                    let frame = self.source.frame();
                    scene.pose(frame.rotation);
                    if let Err(e) = scene.save(shot) {
                        self.error = Some(e);
                    }
                    event_loop.exit();
                }
                self.scene = Some(scene);
            }
            Err(e) => {
                self.error = Some(e);
                event_loop.exit();
            }
        }
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _: WindowId, event: WindowEvent) {
        let Some(scene) = &mut self.scene else { return };
        match event {
            WindowEvent::CloseRequested => {
                self.source.key(LiveKey::Quit);
                event_loop.exit();
            }
            WindowEvent::Resized(size) => scene.resize(size.width, size.height),
            WindowEvent::RedrawRequested => {
                let frame = self.source.frame();
                if let Some(title) = frame.title {
                    scene.window.set_title(&title);
                }
                if frame.quit {
                    self.source.key(LiveKey::Quit);
                    event_loop.exit();
                    return;
                }
                scene.pose(frame.rotation);
                if let Err(e) = scene.render() {
                    self.error = Some(e);
                    event_loop.exit();
                    return;
                }
                scene.window.request_redraw();
            }
            WindowEvent::KeyboardInput {
                event:
                    KeyEvent {
                        physical_key: PhysicalKey::Code(code),
                        state: ElementState::Pressed,
                        repeat: false,
                        ..
                    },
                ..
            } => match code {
                KeyCode::KeyR => self.source.key(LiveKey::Recenter),
                KeyCode::KeyM => self.source.key(LiveKey::Marker),
                KeyCode::KeyC => self.source.key(LiveKey::Calibrate),
                KeyCode::Escape => {
                    self.source.key(LiveKey::Quit);
                    event_loop.exit();
                }
                _ => {}
            },
            _ => {}
        }
    }
}

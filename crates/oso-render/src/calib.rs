//! Camera calibration from `assets/calib/calib.json` (written by tools/calibrate.py, see docs/data-format.md).
//! Only the fields the viewer uses are read.
//!
//! ponytail: assumes the principal point is the image center and ignores camera roll (the orbit camera has
//! none). Fine for the synthetic cameras and near-level fixed cameras; for real off-center K use an off-axis
//! projection built from K, and a roll-capable camera.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct Calib {
    pub version: u32,
    pub cameras: Vec<CalibCamera>,
}

#[derive(Debug, Deserialize)]
pub struct CalibCamera {
    pub id: String,
    pub image_size: [u32; 2],
    #[serde(rename = "K")]
    pub k: [[f32; 3]; 3],
    // Court -> camera rotation (OpenCV: x right, y down, z forward)
    #[serde(rename = "R")]
    pub r: [[f32; 3]; 3],
    pub position: [f32; 3],
}

pub fn parse(bytes: &[u8]) -> anyhow::Result<Calib> {
    let calib: Calib = serde_json::from_slice(bytes)?;
    anyhow::ensure!(
        calib.version == 1,
        "unsupported calib version {}",
        calib.version
    );
    Ok(calib)
}

impl CalibCamera {
    // Optical axis in court coordinates: camera +Z is the third row of R (R maps court to camera)
    fn axis(&self) -> [f32; 3] {
        self.r[2]
    }

    // Where the optical axis meets the floor (y = 0); 10 m along the axis if it never does
    pub fn look_target(&self) -> [f32; 3] {
        let (p, a) = (self.position, self.axis());
        let s = if a[1] < -1e-3 { -p[1] / a[1] } else { 10.0 };
        [p[0] + a[0] * s, p[1] + a[1] * s, p[2] + a[2] * s]
    }

    pub fn fovy(&self) -> f32 {
        2.0 * (self.image_size[1] as f32 / (2.0 * self.k[1][1])).atan()
    }

    pub fn aspect(&self) -> f32 {
        self.image_size[0] as f32 / self.image_size[1] as f32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &[u8] = include_bytes!("../../../assets/calib/calib.json");

    fn cam(position: [f32; 3], r: [[f32; 3]; 3], fy: f32) -> CalibCamera {
        CalibCamera {
            id: "x".into(),
            image_size: [1920, 1080],
            k: [[fy, 0.0, 960.0], [0.0, fy, 540.0], [0.0, 0.0, 1.0]],
            r,
            position,
        }
    }

    #[test]
    fn parses_sample() {
        let c = parse(SAMPLE).unwrap();
        assert_eq!(c.cameras.len(), 2);
        assert_eq!(c.cameras[0].id, "a");
        // Both cameras sit on the -Z side and aim at a floor point inside the court
        for cam in &c.cameras {
            let t = cam.look_target();
            assert!(cam.position[2] < -3.05, "{:?}", cam.position);
            assert!(
                t[0].abs() < 6.7 && t[2].abs() < 3.05 && t[1].abs() < 1e-3,
                "{t:?}"
            );
        }
        assert!((c.cameras[0].aspect() - 16.0 / 9.0).abs() < 1e-3);
    }

    #[test]
    fn sample_position_matches_rotation_row() {
        // position == -R^T t, and t is not parsed: check the look target lies on the ray from position along axis
        let c = parse(SAMPLE).unwrap();
        let cam = &c.cameras[0];
        let (t, ax) = (cam.look_target(), cam.axis());
        let d: Vec<f32> = (0..3).map(|i| t[i] - cam.position[i]).collect();
        let n = d.iter().map(|v| v * v).sum::<f32>().sqrt();
        let dot: f32 = d.iter().zip(ax).map(|(a, b)| a * b).sum::<f32>() / n;
        assert!(dot > 0.999, "{dot}");
    }

    #[test]
    fn look_target_for_camera_at_minus_x() {
        // Looking at the origin from (-10, 4, 0): z = (10, -4, 0)/|.|, x = z cross up (right) = (0, 0, 1)... as rows
        let n = (116.0f32).sqrt();
        let z = [10.0 / n, -4.0 / n, 0.0];
        let x = [0.0, 0.0, 1.0];
        // y (down) = z cross x
        let y = [
            z[1] * x[2] - z[2] * x[1],
            z[2] * x[0] - z[0] * x[2],
            z[0] * x[1] - z[1] * x[0],
        ];
        let t = cam([-10.0, 4.0, 0.0], [x, y, z], 1000.0).look_target();
        assert!(t.iter().all(|v| v.abs() < 1e-3), "{t:?}");
    }

    #[test]
    fn horizon_axis_falls_back_to_ten_meters() {
        let t = cam(
            [0.0, 2.0, 0.0],
            [[0.0; 3], [0.0; 3], [1.0, 0.0, 0.0]],
            1000.0,
        )
        .look_target();
        assert_eq!(t, [10.0, 2.0, 0.0]);
    }

    #[test]
    fn fovy_matches_formula() {
        let f = cam([0.0; 3], [[0.0; 3]; 3], 1000.0).fovy();
        assert!((f - 2.0 * 0.54f32.atan()).abs() < 1e-6);
    }

    #[test]
    fn invalid_or_missing_data_is_an_error() {
        assert!(parse(b"").is_err());
        assert!(parse(b"{\"version\": 2, \"cameras\": []}").is_err());
    }
}

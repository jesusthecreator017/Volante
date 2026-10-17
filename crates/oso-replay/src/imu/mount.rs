//! How the IMU sits in the racquet: a rotation from the sensor frame to the racquet frame (+Y = shaft toward the tip,
//! +Z = string-face normal, +X = Y x Z), applied to every accelerometer and gyro vector before fusion. Shared by the live
//! view and the offline path (`oso-imu`).
//!
//! Choices:
//! - `identity`: the synthetic data (sensor axes = racquet axes). Tests and the synthetic pipeline pass it explicitly.
//! - `flat` (the default for real devices): the MPU lies flat in the pod, so the sensor +Z (the board normal) runs along
//!   the shaft, and the sensor +X is taken as the string-face normal. The shaft is the user's report; the face-normal axis
//!   is a guess, which is what the `C` calibration replaces.
//! - a file written by the calibration (`~/.config/oso/mount.json`): `{"shaft": [x,y,z], "normal": [x,y,z]}`, the racquet's
//!   +Y and +Z axes expressed in the sensor frame.
//!
//! Precedence: `--mount` flag > `~/.config/oso/mount.json` > `flat`.

use super::packet::Sample;
use super::{Vec3, cross, dot, norm};
use std::path::{Path, PathBuf};

/// A gyro above this is not "held still"
pub const STILL_GYRO_DPS: f64 = 20.0;
/// The upright and flat poses must differ by at least this
pub const MIN_POSE_SEPARATION_DEG: f64 = 30.0;
/// Samples averaged for a pose: 1 s at 500 Hz, at least this many must be present
pub const POSE_SAMPLES: usize = 500;
const MIN_POSE_SAMPLES: usize = 400;

/// Rows = the racquet's X, Y, Z axes in the sensor frame, so `apply(v)` = coordinates of `v` in the racquet frame
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mount {
    rows: [Vec3; 3],
}

impl Mount {
    pub const IDENTITY: Mount = Mount { rows: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]] };
    /// Sensor +Z -> racquet +Y (shaft), sensor +X -> racquet +Z (face normal), so racquet X = sensor Y
    pub const FLAT: Mount = Mount { rows: [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]] };

    /// From the racquet's shaft and face-normal axes in the sensor frame (any lengths). The normal is orthonormalised
    /// against the shaft.
    pub fn from_axes(shaft: Vec3, normal: Vec3) -> anyhow::Result<Mount> {
        anyhow::ensure!(norm(shaft) > 1e-9, "shaft axis has no length");
        let y = shaft.map(|c| c / norm(shaft));
        let d = dot(normal, y);
        let z = std::array::from_fn(|i| normal[i] - d * y[i]);
        anyhow::ensure!(norm(z) > 1e-6, "face normal is parallel to the shaft");
        let z = z.map(|c| c / norm(z));
        Ok(Mount { rows: [cross(y, z), y, z] })
    }

    pub fn is_identity(&self) -> bool {
        *self == Self::IDENTITY
    }

    pub fn vec(&self, v: Vec3) -> Vec3 {
        std::array::from_fn(|i| dot(self.rows[i], v))
    }

    /// Accelerometer and gyro rotated into the racquet frame (the saturation flag stays: it is about the raw axes)
    pub fn sample(&self, s: &Sample) -> Sample {
        let f = |v: [f32; 3]| self.vec(v.map(f64::from)).map(|c| c as f32);
        Sample { acc: f(s.acc), gyro: f(s.gyro), ..*s }
    }

    pub fn apply_all(&self, samples: &mut [Sample]) {
        if !self.is_identity() {
            samples.iter_mut().for_each(|s| *s = self.sample(s));
        }
    }

    /// The racquet's shaft and face-normal axes in the sensor frame
    fn axes(&self) -> (Vec3, Vec3) {
        (self.rows[1], self.rows[2])
    }

    pub fn to_json(&self) -> String {
        let (shaft, normal) = self.axes();
        serde_json::to_string_pretty(&serde_json::json!({ "shaft": shaft, "normal": normal })).unwrap_or_default()
    }

    pub fn from_json(text: &str) -> anyhow::Result<Mount> {
        let v: serde_json::Value = serde_json::from_str(text)?;
        let axis = |k: &str| -> anyhow::Result<Vec3> {
            serde_json::from_value(v[k].clone()).map_err(|e| anyhow::anyhow!("mount.json: {k}: {e}"))
        };
        Self::from_axes(axis("shaft")?, axis("normal")?)
    }

    pub fn load(path: &Path) -> anyhow::Result<Mount> {
        Self::from_json(&std::fs::read_to_string(path).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?)
    }

    pub fn save(&self, path: &Path) -> anyhow::Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        std::fs::write(path, self.to_json()).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))
    }

    /// `~/.config/oso/mount.json`
    pub fn default_path() -> Option<PathBuf> {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config/oso/mount.json"))
    }

    /// `--mount` value > the saved file > flat. The flag is `identity`, `flat` or a file path.
    pub fn resolve(flag: Option<&str>) -> anyhow::Result<Mount> {
        Self::resolve_in(flag, Self::default_path().as_deref())
    }

    pub fn resolve_in(flag: Option<&str>, saved: Option<&Path>) -> anyhow::Result<Mount> {
        match flag {
            Some("identity") => Ok(Self::IDENTITY),
            Some("flat") => Ok(Self::FLAT),
            Some(file) => Self::load(Path::new(file)),
            None => match saved.filter(|p| p.exists()) {
                Some(p) => Self::load(p),
                None => Ok(Self::FLAT),
            },
        }
    }
}

/// Unit direction of the accelerometer (= up, at rest) in the sensor frame, averaged over `samples` (raw, before any
/// mount). Err with the reason when the racquet was not still or the data is short.
pub fn rest_direction(samples: &[Sample]) -> Result<Vec3, String> {
    if samples.len() < MIN_POSE_SAMPLES {
        return Err(format!("only {} samples, need {MIN_POSE_SAMPLES}", samples.len()));
    }
    let limit = STILL_GYRO_DPS.to_radians();
    if samples.iter().any(|s| norm(s.gyro.map(f64::from)) > limit) {
        return Err(format!("not still (gyro above {STILL_GYRO_DPS} dps)"));
    }
    let n = samples.len() as f64;
    let mean: Vec3 = std::array::from_fn(|a| samples.iter().map(|s| f64::from(s.acc[a])).sum::<f64>() / n);
    let g = norm(mean) / super::G;
    if !(0.8..=1.2).contains(&g) {
        return Err(format!("acceleration is {g:.2} g, not 1 g (moving?)"));
    }
    Ok(mean.map(|c| c / norm(mean)))
}

/// The two-step calibration: racquet upright with the tip up (shaft axis = up), then lying flat with the strings up
/// (face normal = up). Shared by the live window (`C`) and `oso-rec calibrate`.
#[derive(Debug, Default, Clone, PartialEq)]
pub enum Calibration {
    #[default]
    Idle,
    /// Waiting for the upright pose
    Upright,
    /// Waiting for the flat pose; the shaft direction so far
    Flat(Vec3),
}

impl Calibration {
    /// What the user should do now
    pub fn prompt(&self) -> &'static str {
        match self {
            Calibration::Idle => "",
            Calibration::Upright => "C: hold the racquet upright, tip up, still; press C",
            Calibration::Flat(_) => "lay the racquet flat, strings up, still; press C",
        }
    }

    pub fn active(&self) -> bool {
        *self != Calibration::Idle
    }

    /// A press with the last second of raw samples. Ok(Some) = finished; Err = this step was rejected and is repeated.
    pub fn press(&mut self, recent: &[Sample]) -> Result<Option<Mount>, String> {
        match self.clone() {
            Calibration::Idle => {
                *self = Calibration::Upright;
                Ok(None)
            }
            Calibration::Upright => {
                *self = Calibration::Flat(rest_direction(recent)?);
                Ok(None)
            }
            Calibration::Flat(shaft) => {
                let normal = rest_direction(recent)?;
                let angle = dot(shaft, normal).clamp(-1.0, 1.0).acos().to_degrees();
                if angle < MIN_POSE_SEPARATION_DEG {
                    return Err(format!("flat pose is only {angle:.0} deg from upright, need {MIN_POSE_SEPARATION_DEG}"));
                }
                let mount = Mount::from_axes(shaft, normal).map_err(|e| e.to_string())?;
                *self = Calibration::Idle;
                Ok(Some(mount))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::{G, from_axis_angle, rotate};

    fn rest(acc: Vec3, n: usize, gyro: Vec3) -> Vec<Sample> {
        (0..n)
            .map(|i| Sample {
                t_dev: i as f64 * 0.002,
                acc: acc.map(|c| (c * G) as f32),
                gyro: gyro.map(|c| c as f32),
                saturated: false,
            })
            .collect()
    }

    /// Mount for a made-up pod orientation, and the accelerometer reading (sensor frame) of a racquet whose racquet-frame
    /// "up" is `up`
    fn truth() -> Mount {
        let q = from_axis_angle([0.3, -0.5, 0.8], 1.1);
        Mount::from_axes(rotate(q, [0.0, 1.0, 0.0]), rotate(q, [0.0, 0.0, 1.0])).unwrap()
    }

    fn reading(m: &Mount, up_in_racquet: Vec3) -> Vec3 {
        // sensor = M^T racquet
        let r = m.rows;
        std::array::from_fn(|k| (0..3).map(|i| r[i][k] * up_in_racquet[i]).sum())
    }

    #[test]
    fn identity_is_a_no_op_and_flat_maps_sensor_z_to_the_shaft() {
        let s = rest([0.1, 0.2, 0.9], 3, [0.3, 0.0, -0.1])[0];
        assert_eq!(Mount::IDENTITY.sample(&s), s);
        let mut v = vec![s];
        Mount::IDENTITY.apply_all(&mut v);
        assert_eq!(v[0], s);
        assert_eq!(Mount::FLAT.vec([0.0, 0.0, 1.0]), [0.0, 1.0, 0.0]);
        assert_eq!(Mount::FLAT.vec([1.0, 0.0, 0.0]), [0.0, 0.0, 1.0]);
        // Right-handed: a proper rotation
        let m = Mount::FLAT.rows;
        assert_eq!(cross(m[0], m[1]), m[2]);
    }

    #[test]
    fn calibration_recovers_the_mount_within_a_degree() {
        let want = truth();
        let mut c = Calibration::Idle;
        assert_eq!(c.press(&[]), Ok(None)); // first C only starts
        assert_eq!(c, Calibration::Upright);
        // Tip up: accelerometer reads +1 g along the racquet +Y; flat strings up: along racquet +Z. A little noise.
        let up = rest(reading(&want, [0.0, 1.0, 0.0]), 500, [0.001; 3]);
        assert_eq!(c.press(&up), Ok(None));
        let flat = rest(reading(&want, [0.0, 0.0, 1.0]), 500, [0.001; 3]);
        let got = c.press(&flat).unwrap().unwrap();
        assert_eq!(c, Calibration::Idle);
        for v in [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.3, -0.2, 0.9]] {
            let (a, b) = (got.vec(v), want.vec(v));
            let err = (dot(a, b) / (norm(a) * norm(b))).clamp(-1.0, 1.0).acos().to_degrees();
            assert!(err < 1.0, "{err} deg");
        }
    }

    #[test]
    fn rejects_motion_short_data_and_similar_poses() {
        assert!(rest_direction(&rest([0.0, 0.0, 1.0], 100, [0.0; 3])).is_err());
        // 30 dps spin
        assert!(rest_direction(&rest([0.0, 0.0, 1.0], 500, [0.0, 0.0, 30f64.to_radians()])).unwrap_err().contains("not still"));
        assert!(rest_direction(&rest([0.0, 0.0, 2.0], 500, [0.0; 3])).unwrap_err().contains("1 g"));
        // Two poses 10 degrees apart: rejected, and the step repeats
        let mut c = Calibration::Upright;
        c.press(&rest([0.0, 0.0, 1.0], 500, [0.0; 3])).unwrap();
        let tilted = [10f64.to_radians().sin(), 0.0, 10f64.to_radians().cos()];
        assert!(c.press(&rest(tilted, 500, [0.0; 3])).unwrap_err().contains("deg from upright"));
        assert!(matches!(c, Calibration::Flat(_)));
        // A rejected upright keeps asking for it
        let mut c = Calibration::Upright;
        assert!(c.press(&rest([0.0, 0.0, 1.0], 500, [0.0, 1.0, 0.0])).is_err());
        assert_eq!(c, Calibration::Upright);
    }

    #[test]
    fn malformed_json_is_an_error_not_a_panic() {
        for bad in ["", "[]", "null", "{}", "{\"shaft\": 1, \"normal\": [0,1,0]}", "{\"shaft\": [1,0], \"normal\": [0,1,0]}", "{\"shaft\": [\"a\",0,0], \"normal\": [0,1,0]}"] {
            assert!(Mount::from_json(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn json_round_trip_and_precedence() {
        let m = truth();
        let back = Mount::from_json(&m.to_json()).unwrap();
        for (a, b) in m.rows.iter().flatten().zip(back.rows.iter().flatten()) {
            assert!((a - b).abs() < 1e-12);
        }
        assert!(Mount::from_json("{\"shaft\": [0,0,0], \"normal\": [1,0,0]}").is_err());
        assert!(Mount::from_json("{\"shaft\": [0,1,0], \"normal\": [0,2,0]}").is_err());

        let dir = std::env::temp_dir().join(format!("oso-mount-test-{}", std::process::id()));
        let saved = dir.join("sub/mount.json");
        m.save(&saved).unwrap();
        let other = dir.join("other.json");
        Mount::from_axes([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]).unwrap().save(&other).unwrap();
        // No flag, no file: flat. File beats the default. Flag beats the file.
        assert_eq!(Mount::resolve_in(None, Some(&dir.join("missing.json"))).unwrap(), Mount::FLAT);
        assert_eq!(Mount::resolve_in(None, None).unwrap(), Mount::FLAT);
        assert_eq!(Mount::resolve_in(None, Some(&saved)).unwrap(), Mount::load(&saved).unwrap());
        assert_eq!(Mount::resolve_in(Some("identity"), Some(&saved)).unwrap(), Mount::IDENTITY);
        assert_eq!(Mount::resolve_in(Some("flat"), Some(&saved)).unwrap(), Mount::FLAT);
        let o = Mount::resolve_in(Some(other.to_str().unwrap()), Some(&saved)).unwrap();
        assert_eq!(o.vec([1.0, 0.0, 0.0]), [0.0, 1.0, 0.0]);
        assert!(Mount::resolve_in(Some("/nonexistent/x.json"), None).is_err());
        std::fs::remove_dir_all(dir).ok();
    }
}

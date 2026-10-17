//! Racquet IMU processing (Phase 4): packets -> samples -> orientation -> time sync -> racquet pose track.
//! Pure CPU code, compiles for wasm but is only used by the native `oso-imu` binary.
//!
//! Quaternions here are `[x, y, z, w]` (the layout stored in replay.bin), unit length, rotating a
//! vector of the local frame into the parent frame: `v_parent = q * v_local * conj(q)`.
//! cgmath's `Quaternion::new` takes `(w, x, y, z)`: convert with [`to_cgmath`] / [`from_cgmath`].

pub mod correct;
pub mod fusion;
pub mod live;
pub mod mount;
pub mod packet;
pub mod pose;
pub mod profile;
pub mod strokes;
pub mod swingdet;
pub mod swings;
pub mod sync;

pub type Quat = [f64; 4];
pub type Vec3 = [f64; 3];

pub const G: f64 = 9.80665;
pub const IDENTITY: Quat = [0.0, 0.0, 0.0, 1.0];

pub fn mul(a: Quat, b: Quat) -> Quat {
    let ([ax, ay, az, aw], [bx, by, bz, bw]) = (a, b);
    [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]
}

pub fn conj([x, y, z, w]: Quat) -> Quat {
    [-x, -y, -z, w]
}

pub fn normalize(q: Quat) -> Quat {
    let n = q.iter().map(|c| c * c).sum::<f64>().sqrt();
    if n < 1e-12 {
        IDENTITY
    } else {
        q.map(|c| c / n)
    }
}

pub fn rotate(q: Quat, v: Vec3) -> Vec3 {
    let p = mul(mul(q, [v[0], v[1], v[2], 0.0]), conj(q));
    [p[0], p[1], p[2]]
}

pub fn from_axis_angle(axis: Vec3, angle: f64) -> Quat {
    let n = (axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]).sqrt();
    let s = (angle / 2.0).sin() / n;
    [axis[0] * s, axis[1] * s, axis[2] * s, (angle / 2.0).cos()]
}

/// Rotation about the court's +Y (up) axis.
pub fn yaw_quat(angle: f64) -> Quat {
    from_axis_angle([0.0, 1.0, 0.0], angle)
}

/// Shortest-arc rotation taking direction `a` to direction `b` (any lengths).
pub fn from_two_vectors(a: Vec3, b: Vec3) -> Quat {
    let (na, nb) = (norm(a), norm(b));
    let (a, b) = (a.map(|c| c / na), b.map(|c| c / nb));
    let d = dot(a, b);
    if d < -0.999_999 {
        // Opposite: any axis perpendicular to a
        let axis = if a[0].abs() < 0.9 {
            [1.0, 0.0, 0.0]
        } else {
            [0.0, 1.0, 0.0]
        };
        return from_axis_angle(cross(a, axis), std::f64::consts::PI);
    }
    let c = cross(a, b);
    normalize([c[0], c[1], c[2], 1.0 + d])
}

pub fn dot(a: Vec3, b: Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub fn cross(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

pub fn norm(a: Vec3) -> f64 {
    dot(a, a).sqrt()
}

/// Spherical interpolation along the shortest arc (nlerp when nearly parallel).
pub fn slerp(a: Quat, mut b: Quat, t: f64) -> Quat {
    let mut d: f64 = a.iter().zip(&b).map(|(x, y)| x * y).sum();
    if d < 0.0 {
        b = b.map(|c| -c);
        d = -d;
    }
    let (ka, kb) = if d > 0.9995 {
        (1.0 - t, t)
    } else {
        let th = d.acos();
        let s = th.sin();
        (((1.0 - t) * th).sin() / s, (t * th).sin() / s)
    };
    normalize(std::array::from_fn(|i| a[i] * ka + b[i] * kb))
}

/// Angle in radians between two orientations.
pub fn angle_between(a: Quat, b: Quat) -> f64 {
    // Via the relative rotation: acos of a dot product loses all precision near 0
    let r = mul(conj(a), b);
    2.0 * (r[0] * r[0] + r[1] * r[1] + r[2] * r[2])
        .sqrt()
        .atan2(r[3].abs())
}

pub fn to_cgmath(q: Quat) -> cgmath::Quaternion<f32> {
    cgmath::Quaternion::new(q[3] as f32, q[0] as f32, q[1] as f32, q[2] as f32)
}

pub fn from_cgmath(q: cgmath::Quaternion<f32>) -> Quat {
    [q.v.x as f64, q.v.y as f64, q.v.z as f64, q.s as f64]
}

#[cfg(test)]
mod tests {
    use super::*;
    use cgmath::{Rotation, Rotation3};

    #[test]
    fn cgmath_round_trip_and_rotation_agree() {
        let q = normalize([0.1, -0.5, 0.3, 0.8]);
        let c = to_cgmath(q);
        let back = from_cgmath(c);
        assert!(q.iter().zip(&back).all(|(a, b)| (a - b).abs() < 1e-6));
        // Same rotation of the same vector
        let v = [0.3, 1.0, -2.0];
        let mine = rotate(q, v);
        let theirs = cgmath::Basis3::from_quaternion(&c).rotate_vector(cgmath::Vector3::new(
            v[0] as f32,
            v[1] as f32,
            v[2] as f32,
        ));
        assert!((mine[0] - theirs.x as f64).abs() < 1e-5);
        assert!((mine[1] - theirs.y as f64).abs() < 1e-5);
        assert!((mine[2] - theirs.z as f64).abs() < 1e-5);
        // x, y, z, w layout: a 90 degree turn about +Y sends +X to -Z
        let q = from_axis_angle([0.0, 1.0, 0.0], std::f64::consts::FRAC_PI_2);
        assert!((q[1] - (std::f64::consts::FRAC_PI_4).sin()).abs() < 1e-12 && q[0] == 0.0);
        let r = rotate(q, [1.0, 0.0, 0.0]);
        assert!(r[0].abs() < 1e-12 && (r[2] + 1.0).abs() < 1e-12);
        let c = cgmath::Quaternion::from_angle_y(cgmath::Rad(std::f32::consts::FRAC_PI_2));
        assert!(angle_between(from_cgmath(c), q) < 1e-6);
    }

    #[test]
    fn slerp_and_two_vectors() {
        let a = IDENTITY;
        let b = from_axis_angle([0.0, 0.0, 1.0], 1.0);
        assert!((angle_between(slerp(a, b, 0.5), a) - 0.5).abs() < 1e-9);
        // Shortest arc even when b is the negated representation
        assert!((angle_between(slerp(a, b.map(|c| -c), 0.5), a) - 0.5).abs() < 1e-9);
        let q = from_two_vectors([0.0, 0.0, 2.0], [0.0, 1.0, 0.0]);
        let r = rotate(q, [0.0, 0.0, 1.0]);
        assert!((r[1] - 1.0).abs() < 1e-12);
        let q = from_two_vectors([0.0, 0.0, 1.0], [0.0, 0.0, -1.0]);
        assert!((rotate(q, [0.0, 0.0, 1.0])[2] + 1.0).abs() < 1e-12);
    }
}

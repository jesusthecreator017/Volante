// Quaternion / vector helpers. Port of app/src/core/quat.ts (itself a port of src/imu/mod.rs).
// Quaternions rotate a local-frame vector into the parent frame: v_parent = q * v_local * conj(q).

#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

public typealias Vec3 = SIMD3<Double>

public let G = 9.80665
public let DEG = Double.pi / 180

@inline(__always) public func dot(_ a: Vec3, _ b: Vec3) -> Double { (a * b).sum() }
@inline(__always) public func norm(_ a: Vec3) -> Double { dot(a, a).squareRoot() }
@inline(__always) public func cross(_ a: Vec3, _ b: Vec3) -> Vec3 {
    Vec3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

public struct Quat: Equatable, Sendable {
    public var x, y, z, w: Double
    public init(_ x: Double, _ y: Double, _ z: Double, _ w: Double) {
        self.x = x; self.y = y; self.z = z; self.w = w
    }
    public static let identity = Quat(0, 0, 0, 1)

    public static func * (a: Quat, b: Quat) -> Quat {
        Quat(
            a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
            a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
            a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z
        )
    }
    public var conj: Quat { Quat(-x, -y, -z, w) }
    public var normalized: Quat {
        let n = (x * x + y * y + z * z + w * w).squareRoot()
        return n < 1e-12 ? .identity : Quat(x / n, y / n, z / n, w / n)
    }
    public func rotate(_ v: Vec3) -> Vec3 {
        let p = self * Quat(v.x, v.y, v.z, 0) * conj
        return Vec3(p.x, p.y, p.z)
    }
    public static func axisAngle(_ axis: Vec3, _ angle: Double) -> Quat {
        let s = sin(angle / 2) / norm(axis)
        return Quat(axis.x * s, axis.y * s, axis.z * s, cos(angle / 2))
    }
    /// Shortest-arc rotation taking direction a to direction b (any lengths).
    public static func twoVectors(_ a: Vec3, _ b: Vec3) -> Quat {
        let an = a / norm(a), bn = b / norm(b)
        let d = dot(an, bn)
        if d < -0.999999 {
            let axis = abs(an.x) < 0.9 ? Vec3(1, 0, 0) : Vec3(0, 1, 0)
            return axisAngle(cross(an, axis), .pi)
        }
        let c = cross(an, bn)
        return Quat(c.x, c.y, c.z, 1 + d).normalized
    }
}

/// Spherical interpolation along the shortest arc (nlerp when nearly parallel).
public func slerp(_ a: Quat, _ b0: Quat, _ t: Double) -> Quat {
    var b = b0
    var d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w
    if d < 0 { b = Quat(-b.x, -b.y, -b.z, -b.w); d = -d }
    let ka: Double, kb: Double
    if d > 0.9995 {
        ka = 1 - t; kb = t
    } else {
        let th = acos(d), s = sin(th)
        ka = sin((1 - t) * th) / s; kb = sin(t * th) / s
    }
    return Quat(a.x * ka + b.x * kb, a.y * ka + b.y * kb, a.z * ka + b.z * kb, a.w * ka + b.w * kb).normalized
}

/// Angle in radians between two orientations.
public func angleBetween(_ a: Quat, _ b: Quat) -> Double {
    let r = a.conj * b
    return 2 * atan2((r.x * r.x + r.y * r.y + r.z * r.z).squareRoot(), abs(r.w))
}

@inline(__always) func clamp1(_ x: Double) -> Double { max(-1, min(1, x)) }

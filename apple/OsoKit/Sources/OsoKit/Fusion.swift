// Gyro bias calibration and 6-axis Madgwick orientation. Port of app/src/core/fusion.ts (src/imu/fusion.rs).
// Madgwick runs in a z-up world internally; output is rotated into the court frame (+Y up). Yaw drifts.
// Rate-aware: the stillness block is 0.1 s of samples (50 at 500 Hz).

#if canImport(Darwin)
import Darwin
#endif

public let defaultBeta = 0.04
let maxDt = 0.05
private let lowConf = 0.5
private let accGateG = 0.1
private let lostLowDtFactor = 4.0
private let lowConfWindowS = 0.5
private let blendTauS = 1.0
private let farS = 30.0
private let minBlocks = 10
private let stillGyroStd = 2 * DEG
private let stillGyroMean = 6 * DEG
private let stillAccG = 0.05

public func blockSize(rateHz: Double) -> Int { max(2, Int((rateHz / 10).rounded())) }

public struct SampleWindow: Equatable, Sendable { public var start, end: Int }

/// First run of ≥ 1 s held still. The final block of the run is dropped (may contain the first motion).
public func findStationary(_ s: [Sample], rateHz: Double = 500) -> SampleWindow? {
    let block = blockSize(rateHz: rateHz)
    var runStart: Int?
    let nBlocks = s.count / block
    for b in 0...nBlocks {
        let still = b < nBlocks && blockIsStill(s, b * block, (b + 1) * block)
        if still && runStart == nil { runStart = b }
        else if !still, let r = runStart {
            if b - r >= minBlocks { return SampleWindow(start: r * block, end: (b - 1) * block) }
            runStart = nil
        }
    }
    return nil
}

public func blockIsStill(_ s: [Sample], _ lo: Int, _ hi: Int) -> Bool {
    guard hi - lo >= 2, s[hi - 1].t - s[lo].t <= 0.12 else { return false }
    let n = Double(hi - lo)
    for axis in 0..<3 {
        var gm = 0.0, am = 0.0
        for i in lo..<hi { gm += s[i].gyro[axis]; am += s[i].acc[axis] / G }
        gm /= n; am /= n
        var gv = 0.0, av = 0.0
        for i in lo..<hi {
            gv += (s[i].gyro[axis] - gm) * (s[i].gyro[axis] - gm)
            av += (s[i].acc[axis] / G - am) * (s[i].acc[axis] / G - am)
        }
        if (gv / n).squareRoot() > stillGyroStd || abs(gm) > stillGyroMean || (av / n).squareRoot() > stillAccG { return false }
    }
    return abs(norm(meanAcc(s, lo, hi)) / G - 1) < stillAccG
}

public func meanAcc(_ s: [Sample], _ lo: Int, _ hi: Int) -> Vec3 {
    var m = Vec3.zero
    for i in lo..<hi { m += s[i].acc }
    return m / Double(hi - lo)
}

public func gyroBias(_ s: [Sample], _ w: SampleWindow) -> Vec3 {
    var b = Vec3.zero
    for i in w.start..<w.end { b += s[i].gyro }
    return b / Double(max(1, w.end - w.start))
}

public struct FusionParams: Sendable {
    public var beta = defaultBeta
    public var twoPass = true
    public var nominalDt = 0.002
    public init(beta: Double = defaultBeta, twoPass: Bool = true, nominalDt: Double = 0.002) {
        self.beta = beta; self.twoPass = twoPass; self.nominalDt = nominalDt
    }
}

/// Madgwick quaternion (w, x, y, z), sensor -> z-up world.
struct MQ { var w, x, y, z: Double }

private func toM(_ q: Quat) -> MQ { MQ(w: q.w, x: q.x, y: q.y, z: q.z) }
private func fromM(_ q: MQ) -> Quat { Quat(q.x, q.y, q.z, q.w) }

/// One Madgwick step (acc: any scale, nil = gyro only).
func madgwickStep(_ q: inout MQ, _ g: Vec3, _ acc: Vec3?, _ dt: Double, _ beta: Double) {
    let (q0, q1, q2, q3) = (q.w, q.x, q.y, q.z)
    var qd = [
        0.5 * (-q1 * g.x - q2 * g.y - q3 * g.z),
        0.5 * (q0 * g.x + q2 * g.z - q3 * g.y),
        0.5 * (q0 * g.y - q1 * g.z + q3 * g.x),
        0.5 * (q0 * g.z + q1 * g.y - q2 * g.x),
    ]
    if let acc, norm(acc) > 1e-9 {
        let a = acc / norm(acc)
        let f1 = 2 * (q1 * q3 - q0 * q2) - a.x
        let f2 = 2 * (q0 * q1 + q2 * q3) - a.y
        let f3 = 2 * (0.5 - q1 * q1 - q2 * q2) - a.z
        let gr = [
            -2 * q2 * f1 + 2 * q1 * f2,
            2 * q3 * f1 + 2 * q0 * f2 - 4 * q1 * f3,
            -2 * q0 * f1 + 2 * q3 * f2 - 4 * q2 * f3,
            2 * q1 * f1 + 2 * q2 * f2,
        ]
        let gn = (gr[0] * gr[0] + gr[1] * gr[1] + gr[2] * gr[2] + gr[3] * gr[3]).squareRoot()
        if gn > 1e-12 { for i in 0..<4 { qd[i] -= beta * gr[i] / gn } }
    }
    let r = [q0 + qd[0] * dt, q1 + qd[1] * dt, q2 + qd[2] * dt, q3 + qd[3] * dt]
    let n = (r[0] * r[0] + r[1] * r[1] + r[2] * r[2] + r[3] * r[3]).squareRoot()
    q = MQ(w: r[0] / n, x: r[1] / n, y: r[2] / n, z: r[3] / n)
}

/// Z-up Madgwick world -> court (+Y up): -90° about X.
public let worldToCourt = Quat.axisAngle(Vec3(1, 0, 0), -.pi / 2)
private let up = Vec3(0, 0, 1)

public struct Track: Sendable {
    public var t: [Double] = []
    /// sensor -> court (+Y up), arbitrary heading
    public var q: [Quat] = []
    /// bias-corrected rate, sensor frame, rad/s
    public var w: [Vec3] = []
    /// 1 trusted, 0.5 low
    public var conf: [Double] = []
}

/// Track state at device time t (slerp between samples); nil outside or inside a gap > maxDt.
public func trackAt(_ tr: Track, _ t: Double) -> (q: Quat, w: Vec3, conf: Double)? {
    var lo = 0, hi = tr.t.count
    while lo < hi {
        let m = (lo + hi) >> 1
        if tr.t[m] <= t { lo = m + 1 } else { hi = m }
    }
    let i = lo
    if i == 0 { return nil }
    if i == tr.t.count { return t == tr.t[i - 1] ? (tr.q[i - 1], tr.w[i - 1], tr.conf[i - 1]) : nil }
    let t0 = tr.t[i - 1], t1 = tr.t[i]
    if t1 - t0 > maxDt { return nil }
    let f = (t - t0) / (t1 - t0)
    return (slerp(tr.q[i - 1], tr.q[i], f), tr.w[i - 1] + (tr.w[i] - tr.w[i - 1]) * f, min(tr.conf[i - 1], tr.conf[i]))
}

private struct PS { var t: Double; var acc: Vec3; var gyro: Vec3 }

private func hermite(_ u: Double) -> (Double, Double, Double, Double) {
    let u2 = u * u, u3 = u2 * u
    return (2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u, 3 * u2 - 2 * u3, u3 - u2)
}

/// One Madgwick pass in list order; Madgwick-frame quaternion after each sample.
private func pass(_ s: [PS], _ bias: Vec3, _ q0: MQ, _ sign: Double, _ p: FusionParams) -> [MQ] {
    var q = q0
    var out: [MQ] = []
    out.reserveCapacity(s.count)
    let gyroAt = { (i: Int) -> Vec3 in (s[i].gyro - bias) * sign }
    for i in s.indices {
        let dt = i > 0 ? s[i].t - s[i - 1].t : 0
        let acc = s[i].acc
        if dt > maxDt {
            let qm = fromM(q)
            q = toM((Quat.twoVectors(qm.rotate(acc), up) * qm).normalized)
        } else if dt > 0 {
            let gated = abs(norm(acc) / G - 1) <= accGateG
            let w0 = gyroAt(i - 1), w1 = gyroAt(i)
            if dt <= 1.5 * p.nominalDt {
                madgwickStep(&q, (w0 + w1) * 0.5, gated ? acc : nil, dt, p.beta)
            } else {
                let slope = { (a: Int, b: Int) -> Vec3 in
                    let h = s[b].t - s[a].t
                    return h > 0 && h <= 1.5 * p.nominalDt ? (gyroAt(b) - gyroAt(a)) / h : .zero
                }
                let m0 = i >= 2 ? slope(i - 2, i - 1) : .zero
                let m1 = i + 1 < s.count ? slope(i, i + 1) : .zero
                let n = max(1, Int((dt / p.nominalDt).rounded()))
                for k in 0..<n {
                    let (h00, h10, h01, h11) = hermite((Double(k) + 0.5) / Double(n))
                    let rate = w0 * h00 + m0 * (h10 * dt) + w1 * h01 + m1 * (h11 * dt)
                    madgwickStep(&q, rate, gated && k + 1 == n ? acc : nil, dt / Double(n), p.beta)
                }
            }
        }
        out.append(q)
    }
    return out
}

/// Disturbance intervals: saturated gyro sample, or a hole of 4+ packets.
func disturbances(_ s: [Sample], _ nominalDt: Double) -> [(Double, Double)] {
    var d: [(Double, Double)] = []
    for i in s.indices {
        if i > 0 && s[i].t - s[i - 1].t >= lostLowDtFactor * nominalDt { d.append((s[i - 1].t, s[i].t)) }
        if s[i].saturated { d.append((s[i].t, s[i].t)) }
    }
    return d.sorted { $0.0 < $1.0 }
}

private func distances(_ dist: [(Double, Double)], _ t: Double) -> (Double, Double) {
    var since = farS, until = farS
    for (a, b) in dist {
        if b <= t { since = min(since, t - b) }
        if a >= t { until = min(until, a - t); break }
        if a < t && t < b { return (0, 0) }
    }
    return (since, until)
}

/// Offline fusion: forward + optional backward pass blended by distance to disturbances.
public func fuse(_ s: [Sample], bias: Vec3, initAcc: Vec3, params p: FusionParams) -> Track {
    var track = Track()
    guard !s.isEmpty else { return track }
    let ps = s.map { PS(t: $0.t, acc: $0.acc, gyro: $0.gyro) }
    let fwd = pass(ps, bias, toM(.twoVectors(initAcc, up)), 1, p)
    let dist = disturbances(s, p.nominalDt)
    var q: [Quat]
    if p.twoPass {
        let last = s.count - 1
        let acc = s[last].acc
        let qf = fromM(fwd[last])
        let qb0 = abs(norm(acc) / G - 1) <= accGateG ? toM((Quat.twoVectors(qf.rotate(acc), up) * qf).normalized) : fwd[last]
        let rev = ps.reversed().map { PS(t: -$0.t, acc: $0.acc, gyro: $0.gyro) }
        let bwd = Array(pass(rev, bias, qb0, -1, p).reversed())
        q = s.indices.map { i in
            let (since, until) = distances(dist, s[i].t)
            let wFwd = 1 / (1 + exp(-2 * (since - until) / blendTauS))
            return slerp(fromM(fwd[i]), fromM(bwd[i]), 1 - wFwd)
        }
    } else {
        q = fwd.map(fromM)
    }
    for i in s.indices {
        let (since, until) = distances(dist, s[i].t)
        let low = p.twoPass ? since < lowConfWindowS && until < lowConfWindowS : since < lowConfWindowS
        track.t.append(s[i].t)
        track.q.append((worldToCourt * q[i]).normalized)
        track.w.append(s[i].gyro - bias)
        track.conf.append(low ? lowConf : 1)
    }
    return track
}

public struct Fused: Sendable {
    public var track: Track
    public var bias: Vec3
    /// nil when no stationary window was found and the bias was assumed zero
    public var window: SampleWindow?
}

/// Stationary window -> bias -> fusion. With `assumeZeroBias` (sources that correct their own bias, like Apple's
/// device motion) a capture without a still second is fused with zero bias instead of being skipped.
public func calibrateAndFuse(_ s: [Sample], params: FusionParams, rateHz: Double, assumeZeroBias: Bool = false) -> Fused? {
    if let w = findStationary(s, rateHz: rateHz) {
        let bias = gyroBias(s, w)
        return Fused(track: fuse(s, bias: bias, initAcc: meanAcc(s, w.start, w.end), params: params), bias: bias, window: w)
    }
    guard assumeZeroBias, let first = s.first else { return nil }
    return Fused(track: fuse(s, bias: .zero, initAcc: first.acc, params: params), bias: .zero, window: nil)
}

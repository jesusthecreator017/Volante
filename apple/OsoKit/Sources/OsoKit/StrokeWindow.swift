// The model window around a swing's gyro peak: -600..+400 ms on the 2 ms grid (500 samples), exactly as tools/strokes/features.py
// `window` builds it (np.interp resampling, nearest-sample saturation flags, edge and hole flags). Only the channels the
// orientation-free features read are built: |w| (dps), |a| (g), gyro and accel saturation.
//
// Differences from the Python dataset pipeline, deliberately kept small:
// - the peak is the app's swing peak (SwingDetector window on bias-removed |w|, `Swing.tPeak`); Python takes the largest raw |w| within
//   500 ms after the live counter's onset (smoothed |w| >= 400 dps, 0.8 s refractory). Same argmax on a clean swing; the app also finds
//   weaker swings (start 300 dps) and may split or merge a double peak differently.
// - the gyro bias is not removed here (Python does not either), only for the peak search.

#if canImport(Darwin)
import Darwin
#endif

public struct StrokeWindowData: Sendable, Equatable {
    public static let rateHz = 500
    public static let dt = 1.0 / 500
    public static let preS = 0.600, postS = 0.400
    public static let count = 500
    /// A gap longer than this inside the window is interpolated across but makes the swing unclassifiable.
    public static let maxHoleS = 0.010

    /// |w| in dps and |a| in g, float32-rounded like the Python float32 window
    public var w: [Double]
    public var a: [Double]
    /// 1 where a gyro / accel axis is at the int16 rail
    public var satGyro: [Double]
    public var satAcc: [Double]
    /// the window leaves the recording
    public var edge: Bool
    /// a gap longer than `maxHoleS` inside the window
    public var hole: Bool

    public init(w: [Double], a: [Double], satGyro: [Double], satAcc: [Double], edge: Bool = false, hole: Bool = false) {
        self.w = w; self.a = a; self.satGyro = satGyro; self.satAcc = satAcc; self.edge = edge; self.hole = hole
    }

    /// Window for a peak at `tPeak` (device clock, s) from samples sorted by time.
    public static func build(_ s: [Sample], tPeak: Double) -> StrokeWindowData? {
        guard let first = s.first?.t, let last = s.last?.t, s.count >= 2 else { return nil }
        let n = count
        let grid = (0..<n).map { tPeak - preS + Double($0) * dt }
        let edge = grid[0] < first || grid[n - 1] > last
        let lo = max(0, lowerBound(s, grid[0]) - 1), hi = min(s.count, lowerBound(s, grid[n - 1]) + 2)
        var hole = false
        if hi - lo > 1 { for i in (lo + 1)..<hi where s[i].t - s[i - 1].t > maxHoleS { hole = true; break } }

        var w = [Double](repeating: 0, count: n), a = w, sg = w, sa = w
        for (k, x) in grid.enumerated() {
            // np.interp: index of the last sample <= x, ends held constant
            let j = upperBound(s, x) - 1
            func lerp(_ f: (Sample) -> Double) -> Double {
                if j < 0 { return f(s[0]) }
                if j >= s.count - 1 { return f(s[s.count - 1]) }
                let slope = (f(s[j + 1]) - f(s[j])) / (s[j + 1].t - s[j].t)
                return slope * (x - s[j].t) + f(s[j])
            }
            // float32 channels like the Python window, then the norms of the rounded components
            let g = [lerp { $0.gyro.x / DEG }, lerp { $0.gyro.y / DEG }, lerp { $0.gyro.z / DEG }].map { Double(Float($0)) }
            let c = [lerp { $0.acc.x / G }, lerp { $0.acc.y / G }, lerp { $0.acc.z / G }].map { Double(Float($0)) }
            w[k] = Double(Float((g[0] * g[0] + g[1] * g[1] + g[2] * g[2]).squareRoot()))
            a[k] = Double(Float((c[0] * c[0] + c[1] * c[1] + c[2] * c[2]).squareRoot()))
            // saturation of the nearest sample (ties go to the later one)
            var near = min(max(lowerBound(s, x), 0), s.count - 1)
            let prev = min(max(near - 1, 0), s.count - 1)
            if abs(s[prev].t - x) < abs(s[near].t - x) { near = prev }
            sg[k] = s[near].saturated ? 1 : 0
            sa[k] = s[near].accSaturated ? 1 : 0
        }
        return StrokeWindowData(w: w, a: a, satGyro: sg, satAcc: sa, edge: edge, hole: hole)
    }

    /// first index with s[i].t > x
    private static func upperBound(_ s: [Sample], _ x: Double) -> Int {
        var lo = 0, hi = s.count
        while lo < hi {
            let m = (lo + hi) >> 1
            if s[m].t <= x { lo = m + 1 } else { hi = m }
        }
        return lo
    }
}

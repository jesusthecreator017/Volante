// The 24 orientation-free features of tools/strokes/features.py `full_features` (FEATURES_VERSION 2), a line-by-line port: norms,
// timing, clipping and spectrum of |w| and |a|, so the pod's mounting in the racquet does not matter. The axis-wise features of the
// Python file (peaks per axis, signs, shaft-roll share, gravity direction) are not ported: the phone's models do not read them.

#if canImport(Darwin)
import Darwin
#endif

public enum StrokeFeatures {
    /// features.py FEATURES_VERSION; a model file with another version is refused.
    public static let version = 2

    public static let names: [String] = [
        "w_peak_dps", "a_peak_g", "buildup_s", "burst_s", "burst_lo_s", "follow_decay", "follow_to20_s", "clip_gyro_n", "clip_acc_n",
        "t_apeak_minus_wpeak_s", "w_prebuild_mean_dps", "band0_log_share", "band1_log_share", "band2_log_share", "band3_log_share",
        "band4_log_share", "band5_log_share", "a_burst_s", "clip_gyro_run_s", "clip_acc_run_s", "w_rise_dps2", "w_fall_dps2",
        "w_energy_pre_share", "a_jerk_peak_gps",
    ]

    static let bandsHz: [(Double, Double)] = [(0.5, 3), (3, 6), (6, 12), (12, 25), (25, 60), (60, 125)]
    static let dt = StrokeWindowData.dt
    static let n = StrokeWindowData.count

    /// cos/sin(2 pi k / 500), k = 0..<500, for the DFT of the 500-sample |w|
    private static let trig: (c: [Double], s: [Double]) = {
        let c = (0..<500).map { cos(2 * Double.pi * Double($0) / 500) }
        return (c, (0..<500).map { sin(2 * Double.pi * Double($0) / 500) })
    }()

    /// Contiguous run of `mask` containing index i -> (first, last).
    private static func run(_ mask: [Bool], _ i: Int) -> (Int, Int) {
        var lo = i, hi = i
        while lo > 0 && mask[lo - 1] { lo -= 1 }
        while hi < mask.count - 1 && mask[hi + 1] { hi += 1 }
        return (lo, hi)
    }

    private static func argmax(_ x: [Double]) -> Int {
        var p = 0
        for i in x.indices where x[i] > x[p] { p = i }
        return p
    }

    private static func mean(_ x: ArraySlice<Double>) -> Double { x.reduce(0, +) / Double(x.count) }

    private static func longestRun(_ f: [Double]) -> Double {
        var best = 0, cur = 0
        for v in f { if v > 0.5 { cur += 1; best = max(best, cur) } else { cur = 0 } }
        return Double(best) * dt
    }

    /// name -> value (float32-rounded like the Python float32 vector).
    public static func compute(_ win: StrokeWindowData) -> [String: Double] {
        precondition(win.w.count == n && win.a.count == n && win.satGyro.count == n && win.satAcc.count == n)
        let w = win.w, a = win.a
        let p = argmax(w)
        let wp = max(w[p], 1e-9)
        var f: [String: Double] = ["w_peak_dps": w[p], "a_peak_g": a.max()!]

        let (b0, _) = run(w.map { $0 > 0.2 * wp }, p)
        f["buildup_s"] = Double(p - b0) * dt
        let (l5, h5) = run(w.map { $0 > 0.5 * wp }, p)
        f["burst_s"] = Double(h5 - l5 + 1) * dt
        let (l2, h2) = run(w.map { $0 > 0.25 * wp }, p)
        f["burst_lo_s"] = Double(h2 - l2 + 1) * dt

        let fa = min(n, p + Int(0.1 * Double(StrokeWindowData.rateHz))), fb = min(n, p + Int(0.3 * Double(StrokeWindowData.rateHz)))
        f["follow_decay"] = fb > fa ? mean(w[fa..<fb]) / wp : 0
        if let k = w[p...].firstIndex(where: { $0 < 0.2 * wp }) { f["follow_to20_s"] = min(0.4, Double(k - p) * dt) } else { f["follow_to20_s"] = 0.4 }

        f["clip_gyro_n"] = win.satGyro.reduce(0, +)
        f["clip_acc_n"] = win.satAcc.reduce(0, +)
        f["t_apeak_minus_wpeak_s"] = Double(argmax(a) - p) * dt
        f["w_prebuild_mean_dps"] = mean(w[0..<Int(0.3 * Double(StrokeWindowData.rateHz))])

        // |w| spectrum: Hann window (np.hanning), DC removed, bins k = 0...250 of rfft (1 Hz apart), energy |X_k|^2
        let mu = mean(w[...])
        let y = (0..<n).map { (w[$0] - mu) * (0.5 - 0.5 * cos(2 * Double.pi * Double($0) / Double(n - 1))) }
        var spec = [Double](repeating: 0, count: n / 2 + 1)
        for k in 0...(n / 2) {
            var re = 0.0, im = 0.0
            for i in 0..<n {
                let j = (k * i) % n
                re += y[i] * trig.c[j]; im -= y[i] * trig.s[j]
            }
            spec[k] = re * re + im * im
        }
        let tot = max(spec.reduce(0, +), 1e-9)
        for (b, band) in bandsHz.enumerated() {
            var e = 0.0
            for k in spec.indices where Double(k) >= band.0 && Double(k) < band.1 { e += spec[k] }
            f["band\(b)_log_share"] = log10(e / tot + 1e-6)
        }

        let dev = a.map { abs($0 - 1.0) }
        let ap = argmax(dev)
        let (la, ha) = run(dev.map { $0 > 0.5 * max(dev[ap], 1e-9) }, ap)
        f["a_burst_s"] = Double(ha - la + 1) * dt
        f["clip_gyro_run_s"] = longestRun(win.satGyro)
        f["clip_acc_run_s"] = longestRun(win.satAcc)

        // |w| smoothed over 5 samples (np.convolve 'same' with a zero-padded box), steepest rise before / fall after the peak
        let ws = (0..<n).map { i -> Double in
            var s = 0.0
            for j in max(0, i - 2)...min(n - 1, i + 2) { s += w[j] }
            return s * 0.2
        }
        let dw = (0..<(n - 1)).map { (ws[$0 + 1] - ws[$0]) / dt }
        f["w_rise_dps2"] = max(0, dw[0..<p].max() ?? 0)
        f["w_fall_dps2"] = min(0, dw[min(p, n - 1)...].min() ?? 0)
        f["w_energy_pre_share"] = w[0..<p].reduce(0) { $0 + $1 * $1 } / max(w.reduce(0) { $0 + $1 * $1 }, 1e-9)
        f["a_jerk_peak_gps"] = (0..<(n - 1)).map { abs((a[$0 + 1] - a[$0]) / dt) }.max()!
        return f.mapValues { Double(Float($0)) }
    }
}

// imu.bin + packet v1 (docs/data-format.md). Port of app/src/core/packet.ts / src/imu/packet.rs.
// 16-byte header ("OSOI", u16 version=1, u16 rate_hz, u32 mount_id, u32 reserved) + 18-byte LE packets
// `u16 seq, u32 t_us, i16 ax ay az gx gy gz` in arrival order.

public let imuHeaderLength = 16
public let packetLength = 18
public let accLsbPerG = 2048.0
public let gyroLsbPerDps = 16.4
let accK = G / accLsbPerG
let gyroK = DEG / gyroLsbPerDps

/// Where the sensor sits: written into the header's `mount_id` (was reserved, 0).
public enum Mount: UInt32, Sendable {
    /// racquet pod, butt of the handle (+Y along the handle, +Z string-bed normal)
    case racquet = 0
    /// Apple Watch on the wrist (watch device-motion axes); tilt/face angles describe the forearm
    case wrist = 1
}

public struct ImuHeader: Equatable, Sendable {
    public var version: UInt16 = 1
    public var rateHz: UInt16
    public var mountId: UInt32
    public var mount: Mount? { Mount(rawValue: mountId) }
}

public struct RawPacket: Equatable, Sendable {
    public var seq16: UInt16
    public var t32: UInt32
    public var v: [Int16] // ax ay az gx gy gz, raw counts
    public init(seq16: UInt16, t32: UInt32, v: [Int16]) { self.seq16 = seq16; self.t32 = t32; self.v = v }

    public init(_ b: [UInt8], at o: Int) {
        let r = LE(b)
        seq16 = r.u16(o); t32 = r.u32(o + 2)
        v = (0..<6).map { r.i16(o + 6 + 2 * $0) }
    }

    func write(into w: inout LEWriter, at o: Int) {
        w.u16(o, seq16); w.u32(o + 2, t32)
        for k in 0..<6 { w.i16(o + 6 + 2 * k, v[k]) }
    }
}

public struct Sample: Sendable {
    /// device clock, s (unwrapped)
    public var t: Double
    /// specific force, sensor frame, m/s² (f32 precision like the Rust reference)
    public var acc: Vec3
    /// angular rate, sensor frame, rad/s, bias NOT removed
    public var gyro: Vec3
    /// a gyro axis at the int16 limit (≥ 2000 dps)
    public var saturated: Bool
    public var accSaturated: Bool

    public init(t: Double, acc: Vec3, gyro: Vec3, saturated: Bool = false, accSaturated: Bool = false) {
        self.t = t; self.acc = acc; self.gyro = gyro; self.saturated = saturated; self.accSaturated = accSaturated
    }

    /// A sample from a packet's raw counts at an unwrapped device time (µs).
    public init(tUs: Double, v: [Int16]) {
        let f = { (x: Double) in Double(Float(x)) }
        let rail = { (x: Int16) in x == .max || x == .min }
        t = tUs * 1e-6
        acc = Vec3(f(Double(v[0]) * accK), f(Double(v[1]) * accK), f(Double(v[2]) * accK))
        gyro = Vec3(f(Double(v[3]) * gyroK), f(Double(v[4]) * gyroK), f(Double(v[5]) * gyroK))
        saturated = rail(v[3]) || rail(v[4]) || rail(v[5])
        accSaturated = rail(v[0]) || rail(v[1]) || rail(v[2])
    }
}

public struct Gap: Sendable { public var tStart, tEnd: Double; public var missing: Int }

public struct PacketStats: Equatable, Sendable {
    public var arrivals = 0, unique = 0, lost = 0, duplicate = 0, reordered = 0
}

public struct Capture: Sendable {
    public var header: ImuHeader
    public var samples: [Sample]
    public var gaps: [Gap]
    public var stats: PacketStats
    public var rateHz: Double { Double(header.rateHz) }
}

public enum ImuFileError: Error, Equatable {
    case tooShort, badMagic, unsupportedVersion(UInt16), partialPacket(Int)
}

/// Unwraps the 16-bit seq and 32-bit µs counters (each step is taken as the shortest signed distance).
public struct Unwrapper: Sendable {
    private var prev: (s16: UInt16, s: Int, t32: UInt32, t: Double)?
    public init() {}

    public mutating func next(_ p: RawPacket) -> (seq: Int, tUs: Double) {
        guard let pr = prev else {
            prev = (p.seq16, Int(p.seq16), p.t32, Double(p.t32))
            return (Int(p.seq16), Double(p.t32))
        }
        let seq = pr.s + Int(Int16(bitPattern: p.seq16 &- pr.s16))
        let tUs = pr.t + Double(Int32(bitPattern: p.t32 &- pr.t32))
        prev = (p.seq16, seq, p.t32, tUs)
        return (seq, tUs)
    }

    /// Unwrap a marker's 32-bit time against the newest packet without advancing; nil before the first packet.
    public func peekTime(_ t32: UInt32) -> Double? {
        prev.map { $0.t + Double(Int32(bitPattern: t32 &- $0.t32)) }
    }
}

public enum ImuFile {
    public static func header(_ b: [UInt8]) throws -> ImuHeader {
        guard b.count >= imuHeaderLength else { throw ImuFileError.tooShort }
        guard b[0..<4].elementsEqual([0x4f, 0x53, 0x4f, 0x49]) else { throw ImuFileError.badMagic }
        let r = LE(b)
        guard r.u16(4) == 1 else { throw ImuFileError.unsupportedVersion(r.u16(4)) }
        return ImuHeader(version: 1, rateHz: r.u16(6), mountId: r.u32(8))
    }

    /// The packet body in arrival order.
    public static func packets(_ b: [UInt8]) throws -> [RawPacket] {
        let body = b.count - imuHeaderLength
        guard body % packetLength == 0 else { throw ImuFileError.partialPacket(body) }
        return stride(from: imuHeaderLength, to: b.count, by: packetLength).map { RawPacket(b, at: $0) }
    }

    /// Full parse: sort by unwrapped seq, drop duplicates (first arrival wins), keep gaps as gaps.
    public static func parse(_ b: [UInt8]) throws -> Capture {
        let h = try header(b)
        return assemble(header: h, packets: try packets(b))
    }

    public static func assemble(header: ImuHeader, packets: [RawPacket]) -> Capture {
        var un = Unwrapper()
        let raws = packets.map { p -> (seq: Int, tUs: Double, v: [Int16]) in
            let u = un.next(p)
            return (u.seq, u.tUs, p.v)
        }
        var stats = PacketStats(arrivals: raws.count)
        var top = Int.min
        var seen = Set<Int>()
        for r in raws {
            if !seen.insert(r.seq).inserted { stats.duplicate += 1 } else if r.seq < top { stats.reordered += 1 }
            top = max(top, r.seq)
        }
        let sorted = raws.enumerated().sorted { ($0.element.seq, $0.offset) < ($1.element.seq, $1.offset) }
        var uniq: [(seq: Int, tUs: Double, v: [Int16])] = []
        for (_, r) in sorted where uniq.last?.seq != r.seq { uniq.append(r) }
        stats.unique = uniq.count
        var gaps: [Gap] = []
        for i in uniq.indices.dropFirst() {
            let missing = uniq[i].seq - uniq[i - 1].seq - 1
            if missing > 0 {
                stats.lost += missing
                gaps.append(Gap(tStart: uniq[i - 1].tUs * 1e-6, tEnd: uniq[i].tUs * 1e-6, missing: missing))
            }
        }
        return Capture(header: header, samples: uniq.map { Sample(tUs: $0.tUs, v: $0.v) }, gaps: gaps, stats: stats)
    }

    /// Writes header + packets in the given (arrival) order.
    public static func encode(rateHz: UInt16, mount: Mount = .racquet, packets: [RawPacket]) -> [UInt8] {
        var w = LEWriter(count: imuHeaderLength + packets.count * packetLength)
        for (k, c) in [0x4f, 0x53, 0x4f, 0x49].enumerated() { w.u8(k, UInt8(c)) }
        w.u16(4, 1); w.u16(6, rateHz); w.u32(8, mount.rawValue)
        for (i, p) in packets.enumerated() { p.write(into: &w, at: imuHeaderLength + i * packetLength) }
        return w.bytes
    }

    /// Splits one imu_stream notification into packets; nil when it isn't a whole number of packets.
    public static func notification(_ b: [UInt8]) -> [RawPacket]? {
        guard !b.isEmpty, b.count % packetLength == 0 else { return nil }
        return stride(from: 0, to: b.count, by: packetLength).map { RawPacket(b, at: $0) }
    }
}

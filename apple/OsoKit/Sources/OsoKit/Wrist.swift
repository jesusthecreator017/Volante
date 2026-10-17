// Apple Watch wrist samples -> the pod's packet format, so a watch session is an ordinary imu.bin (mount_id 1).
// Input is CoreMotion device motion: userAcceleration + gravity in g (Apple sign: a device lying face up reads
// gravity z = -1), rotationRate in rad/s (already bias-corrected by CoreMotion). The pod's MPU reports specific force
// (+1 g on z face up), so acceleration is negated. Values are quantized to the pod's scales (±16 g, ±2000 dps).

public struct WristMotion: Sendable {
    /// seconds, any monotonic clock (CMSensorDataBatch timestamps)
    public var t: Double
    public var userAcc: Vec3
    public var gravity: Vec3
    public var rotationRate: Vec3
    public init(t: Double, userAcc: Vec3, gravity: Vec3, rotationRate: Vec3) {
        self.t = t; self.userAcc = userAcc; self.gravity = gravity; self.rotationRate = rotationRate
    }
}

public struct WristEncoder: Sendable {
    public let rateHz: UInt16
    public private(set) var packets: [RawPacket] = []
    private var t0: Double?
    private var seq: UInt16 = 0

    public init(rateHz: UInt16 = 200) { self.rateHz = rateHz }

    private static func counts(_ x: Double) -> Int16 {
        Int16(max(Double(Int16.min), min(Double(Int16.max), x.rounded())))
    }

    /// Converts one device-motion sample to a packet (and keeps it for the file).
    @discardableResult
    public mutating func append(_ m: WristMotion) -> RawPacket {
        if t0 == nil { t0 = m.t }
        let acc = (m.userAcc + m.gravity) * -accLsbPerG
        let gyro = m.rotationRate * (gyroLsbPerDps / DEG)
        let tUs = ((m.t - t0!) * 1e6).rounded()
        let p = RawPacket(
            seq16: seq, t32: UInt32(truncatingIfNeeded: Int64(tUs)),
            v: [acc.x, acc.y, acc.z, gyro.x, gyro.y, gyro.z].map(Self.counts)
        )
        seq &+= 1
        packets.append(p)
        return p
    }

    /// Seconds from the first sample: the session's device-clock origin.
    public var startTime: Double? { t0 }

    public func file() -> [UInt8] { ImuFile.encode(rateHz: rateHz, mount: .wrist, packets: packets) }
}

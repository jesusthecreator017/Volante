// The racquet pod's BLE contract: ../../docs/ble-profile.md, constants mirrored from ../../proto/src/lib.rs.
// Transport-free: the iPhone app's CoreBluetooth link (OsoPhone/PodLink.swift) feeds bytes in and out of here.

public enum PodUUID {
    public static let service = "6F736F00-0001-4C45-8A00-6261646D696E"
    public static let imuStream = "6F736F00-0002-4C45-8A00-6261646D696E"
    public static let control = "6F736F00-0003-4C45-8A00-6261646D696E"
    public static let status = "6F736F00-0004-4C45-8A00-6261646D696E"
    public static let batteryService = "180F"
    public static let batteryLevel = "2A19"
}

/// `control` opcodes (proto `op`).
public enum PodCommand: UInt8, Sendable {
    case startStream = 0x01, stopStream = 0x02, startRecording = 0x03, stopRecording = 0x04, upload = 0x05
    case syncMark = 0x06
    /// Host confirms a complete upload; only then does the next power-on record over the session.
    case markUploaded = 0x07
    case sleep = 0x08
    /// Only honored while sleeping: reset into the normal boot.
    case wake = 0x09
}

public enum PodEvent: UInt8, Sendable {
    case none = 0, markerTap = 1, markerCommand = 2, uploadDone = 3, error = 4
}

public enum PodError: UInt32, Sendable, CustomStringConvertible {
    case badOpcode = 1, noSession = 2, needConfirm = 3, busy = 4, sleeping = 5
    public var description: String {
        switch self {
        case .badOpcode: "unknown command"
        case .noSession: "no stored session (or nothing to mark uploaded)"
        case .needConfirm: "the stored session was not uploaded yet: repeat within 5 s to overwrite it"
        case .busy: "busy (erasing flash or uploading)"
        case .sleeping: "the pod is sleeping: wake it first"
        }
    }
}

/// `status` characteristic: 24 bytes, little-endian.
public struct PodStatus: Equatable, Sendable {
    public static let length = 24
    public enum Bit {
        public static let streaming: UInt8 = 1, recording: UInt8 = 2, uploading: UInt8 = 4, pendingUpload: UInt8 = 8
        public static let flashFull: UInt8 = 16, imuOk: UInt8 = 32, imuIs6500: UInt8 = 64, erasing: UInt8 = 128
    }
    public static let extSleeping: UInt8 = 1

    public var fwMajor: UInt8 = 0, fwMinor: UInt8 = 1
    public var rateHz: UInt16 = 500
    /// 255 = unknown (no battery divider fitted yet)
    public var batteryRaw: UInt8 = 255
    public var stateBits: UInt8 = 0
    public var accelG: UInt8 = 16
    public var gyroDps: UInt16 = 2000
    public var bufferedPackets: UInt32 = 0
    public var fifoOverflows: UInt16 = 0
    public var eventRaw: UInt8 = 0
    public var eventValue: UInt32 = 0
    public var stateExt: UInt8 = 0

    public init() {}

    public init?(_ b: [UInt8]) {
        guard b.count >= Self.length else { return nil }
        let r = LE(b)
        fwMajor = r.u8(0); fwMinor = r.u8(1); rateHz = r.u16(2); batteryRaw = r.u8(4); stateBits = r.u8(5)
        accelG = r.u8(6); gyroDps = r.u16(7); bufferedPackets = r.u32(9); fifoOverflows = r.u16(13)
        eventRaw = r.u8(15); eventValue = r.u32(16); stateExt = r.u8(20)
    }

    public func encoded() -> [UInt8] {
        var w = LEWriter(count: Self.length)
        w.u8(0, fwMajor); w.u8(1, fwMinor); w.u16(2, rateHz); w.u8(4, batteryRaw); w.u8(5, stateBits)
        w.u8(6, accelG); w.u16(7, gyroDps); w.u32(9, bufferedPackets); w.u16(13, fifoOverflows)
        w.u8(15, eventRaw); w.u32(16, eventValue); w.u8(20, stateExt)
        return w.bytes
    }

    public var battery: Int? { batteryRaw == 255 ? nil : Int(batteryRaw) }
    public var event: PodEvent? { PodEvent(rawValue: eventRaw) }
    public var error: PodError? { event == .error ? PodError(rawValue: eventValue) : nil }
    public var firmware: String { "\(fwMajor).\(fwMinor)" }
    private func bit(_ m: UInt8) -> Bool { stateBits & m != 0 }
    public var streaming: Bool { bit(Bit.streaming) }
    public var recording: Bool { bit(Bit.recording) }
    public var uploading: Bool { bit(Bit.uploading) }
    public var pendingUpload: Bool { bit(Bit.pendingUpload) }
    public var flashFull: Bool { bit(Bit.flashFull) }
    public var imuOk: Bool { bit(Bit.imuOk) }
    public var imuIs6500: Bool { bit(Bit.imuIs6500) }
    public var erasing: Bool { bit(Bit.erasing) }
    public var sleeping: Bool { stateExt & Self.extSleeping != 0 }

    /// Human summary of the state bits, for the Device screen.
    public var stateLabels: [String] {
        if sleeping { return ["sleeping"] }
        var out: [String] = []
        if streaming { out.append("streaming") }
        if recording { out.append("recording") }
        if erasing { out.append("erasing flash") }
        if uploading { out.append("uploading") }
        if pendingUpload { out.append("session not uploaded") }
        if flashFull { out.append("flash full") }
        return out.isEmpty ? ["idle"] : out
    }
}

/// Packets per notification for an ATT MTU (the firmware sends at most 13).
public func packetsPerNotification(mtu: Int) -> Int { min(13, (mtu - 3) / 18) }

// MARK: little-endian helpers

struct LE {
    let b: [UInt8]
    init(_ b: [UInt8]) { self.b = b }
    func u8(_ o: Int) -> UInt8 { b[o] }
    func u16(_ o: Int) -> UInt16 { UInt16(b[o]) | UInt16(b[o + 1]) << 8 }
    func i16(_ o: Int) -> Int16 { Int16(bitPattern: u16(o)) }
    func u32(_ o: Int) -> UInt32 {
        UInt32(b[o]) | UInt32(b[o + 1]) << 8 | UInt32(b[o + 2]) << 16 | UInt32(b[o + 3]) << 24
    }
}

struct LEWriter {
    var bytes: [UInt8]
    init(count: Int) { bytes = [UInt8](repeating: 0, count: count) }
    mutating func u8(_ o: Int, _ v: UInt8) { bytes[o] = v }
    mutating func u16(_ o: Int, _ v: UInt16) { bytes[o] = UInt8(v & 0xff); bytes[o + 1] = UInt8(v >> 8) }
    mutating func i16(_ o: Int, _ v: Int16) { u16(o, UInt16(bitPattern: v)) }
    mutating func u32(_ o: Int, _ v: UInt32) {
        for k in 0..<4 { bytes[o + k] = UInt8((v >> (8 * UInt32(k))) & 0xff) }
    }
}

// The Arena API's types (site/src/types.ts, arena/handoff/PROMPT.md section 7 + 14), decoded with convertFromSnakeCase.
// Fields newer servers add are optional, so an older server still decodes.

import Foundation

enum ArenaLadder: String, Codable, CaseIterable, Identifiable, Hashable {
    case open, twins
    var id: String { rawValue }
    var title: String { self == .open ? "Open" : "Twins" }
}

struct ArenaUser: Codable, Hashable, Identifiable {
    var id: Int
    var login: String
    var avatarUrl: String
}

/// A GitHub user or a house player ({"house": true}).
enum ArenaOwner: Codable, Hashable {
    case user(ArenaUser)
    case house

    init(from decoder: Decoder) throws {
        struct House: Decodable { var house: Bool? }
        if (try? House(from: decoder))?.house == true { self = .house; return }
        self = .user(try ArenaUser(from: decoder))
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .user(let u): try u.encode(to: encoder)
        case .house:
            var c = encoder.singleValueContainer()
            try c.encode(["house": true])
        }
    }

    var name: String { if case .user(let u) = self { u.login } else { "House" } }
    var avatar: URL? { if case .user(let u) = self { URL(string: u.avatarUrl) } else { nil } }
    var isHouse: Bool { if case .house = self { true } else { false } }
}

struct ArenaBuild: Codable, Hashable {
    var power: Int, speed: Int, reach: Int, accuracy: Int
}

struct ArenaTwin: Codable, Hashable {
    struct Profile: Codable, Hashable {
        struct Source: Codable, Hashable { var sessions: Int; var swings: Int; var minutes: Double }
        struct Speed: Codable, Hashable { var p50Kmh: Double; var p90Kmh: Double; var cv: Double; var clippedFrac: Double }
        struct Tempo: Codable, Hashable { var medianGapS: Double; var p25GapS: Double; var p75GapS: Double }
        var source: Source
        var speed: Speed
        var tempo: Tempo
        var strokeMix: [String: Double]?
    }
    struct Fidelity: Codable, Hashable {
        var ok: Bool
        var tempoRatio: Double?
        var mixJs: Double?
        var checkedAt: String?
        var reasons: [String]?
    }
    var profile: Profile
    var fidelity: Fidelity
}

struct ArenaModel: Codable, Hashable, Identifiable {
    var id: Int
    var name: String
    var owner: ArenaOwner
    var build: ArenaBuild
    var status: String
    var rating: Double?
    var games: Int
    var ladder: ArenaLadder?
    var twin: ArenaTwin?
}

struct ArenaModelDetail: Codable, Hashable {
    struct Point: Codable, Hashable { var t: String; var rating: Double; var matchId: Int }
    var id: Int
    var name: String
    var owner: ArenaOwner
    var build: ArenaBuild
    var status: String
    var rating: Double?
    var games: Int
    var ladder: ArenaLadder?
    var twin: ArenaTwin?
    var description: String?
    var createdAt: String
    var retiredAt: String?
    var seasonId: Int
    var rejectReasons: [String]?
    var ratingHistory: [Point]

    var summary: ArenaModel { .init(id: id, name: name, owner: owner, build: build, status: status, rating: rating, games: games, ladder: ladder, twin: twin) }
}

struct ArenaLadderPage: Codable {
    struct Item: Codable, Hashable, Identifiable {
        var rank: Int
        var model: ArenaModel
        var rating: Double
        var provisional: Bool
        var games: Int
        var wins: Int
        var losses: Int
        var id: Int { model.id }
    }
    var total: Int
    var items: [Item]
}

struct ArenaRally: Codable, Hashable {
    var game: Int, tStart: Double, tEnd: Double, server: String, winner: String, reason: String, shots: Int, score: [Int]
}

struct ArenaMatch: Codable, Hashable, Identifiable {
    struct Delta: Codable, Hashable { var a: Double; var b: Double }
    var id: Int
    var kind: String
    var status: String
    var createdAt: String
    var finishedAt: String?
    var a: ArenaModel
    var b: ArenaModel
    var gamesWon: [Int]
    var winner: String?
    var games: [[Int]]
    var ratingDelta: Delta?
    var ladder: ArenaLadder?
    var rallies: [ArenaRally]?
}

struct ArenaMatchPage: Codable { var items: [ArenaMatch]; var nextBefore: Int? }

struct ArenaTie: Codable, Hashable {
    var seedA: Int?, seedB: Int?
    var a: ArenaModel?, b: ArenaModel?
    var matchId: Int?
    var winner: String?
}

struct ArenaTournament: Codable, Hashable, Identifiable {
    var id: Int
    var seasonId: Int
    var startsAt: String
    var status: String
    var ladder: ArenaLadder?
    var rounds: [[ArenaTie]]?
}

struct ArenaMe: Codable {
    var user: ArenaUser?
    var csrf: String
    var uploadsLeftToday: Int?
}

struct ArenaItems<T: Codable>: Codable { var items: [T] }
struct ArenaUploadResult: Codable { var id: Int; var status: String }
struct ArenaAppSession: Codable { var session: String; var user: ArenaUser }
struct ArenaError: Codable { var error: String; var detail: String? }

/// What a GitHub sign-in failure reason means (the server sends volante://auth?error=<reason>; same table as the site).
enum ArenaSignInReason {
    static func text(_ r: String) -> String {
        switch r {
        case "denied": "You cancelled the GitHub sign-in."
        case "no_state_cookie": "The sign-in browser didn't keep its cookie. Try again."
        case "state_expired", "state_mismatch": "The sign-in took too long or was out of date. Try again."
        case "bad_verification_code": "GitHub's sign-in code had expired. Try again."
        case "incorrect_client_credentials", "redirect_uri_mismatch": "Sign-in is misconfigured on the server. The Arena owner needs to fix it."
        case "github_unreachable": "The server couldn't reach GitHub. Try again in a minute."
        case "banned": "This account can't sign in."
        default: "Sign-in didn't work (\(r)). Try again."
        }
    }
}

extension String {
    /// ISO 8601 (with or without fractional seconds) -> Date
    var arenaDate: Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: self) { return d }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: self)
    }
}

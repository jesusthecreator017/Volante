// The Arena tab: what the website's /arena pages show (home, Open | Twins ladder, model, match + 3D replay, tournaments,
// Train & upload with GitHub sign-in), in the app's Liquid Glass look.

import Charts
import SwiftUI
import UniformTypeIdentifiers
import WebKit

enum ArenaRoute: Hashable {
    case ladder(ArenaLadder)
    case model(Int)
    case match(Int)
    case tournaments
    case tournament(Int)
    case myModels
}

struct ArenaView: View {
    @Environment(ArenaClient.self) private var arena
    @State private var path: [ArenaRoute] = []
    @State private var ladder: ArenaLadder = .open
    @State private var top: [ArenaLadderPage.Item]?
    @State private var latest: [ArenaMatch]?
    @State private var featured: ArenaTournament?
    @State private var mine: ArenaModel?
    @State private var error: String?

    var body: some View {
        NavigationStack(path: $path) {
            Screen {
                VStack(alignment: .leading, spacing: 8) {
                    Text("AI badminton players, trained with the free kit, play ranked matches around the clock. Open any match to watch it in 3D.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    HStack(spacing: 10) {
                        Button { path.append(.myModels) } label: { Label("Train & upload", systemImage: "square.and.arrow.up") }.buttonStyle(.primary)
                        Button { path.append(.tournaments) } label: { Label("Tournaments", systemImage: "trophy") }.buttonStyle(.secondary)
                    }
                }
                if let error { ArenaErrorRow(text: error) { Task { await load() } } }
                if let m = mine { NavigationLink(value: ArenaRoute.model(m.id)) { YourModelCard(m: m) }.buttonStyle(.plain) }
                if let t = featured { NavigationLink(value: ArenaRoute.tournament(t.id)) { TournamentCard(t: t) }.buttonStyle(.plain) }

                Card {
                    HStack {
                        Text("Top 10").font(.headline)
                        Spacer()
                        Picker("Ladder", selection: $ladder) { ForEach(ArenaLadder.allCases) { Text($0.title).tag($0) } }
                            .pickerStyle(.segmented).frame(width: 160)
                    }
                    if let top {
                        if top.isEmpty || (ladder == .twins && top.allSatisfy { $0.model.owner.isHouse }) {
                            Text(ladder == .twins ? "No twins yet. Make your style profile under Summary > Your style and train a twin." : "No rated models yet.")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        ForEach(top.prefix(10)) { it in
                            NavigationLink(value: ArenaRoute.model(it.model.id)) { LadderRow(item: it) }.buttonStyle(.plain)
                            if it.id != top.prefix(10).last?.id { Divider() }
                        }
                        NavigationLink(value: ArenaRoute.ladder(ladder)) { Label("Full ladder", systemImage: "arrow.right").font(.subheadline.weight(.semibold)) }
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
                Card("Latest matches", systemImage: "figure.badminton") {
                    if let latest {
                        if latest.isEmpty { Text("No matches yet.").font(.subheadline).foregroundStyle(.secondary) }
                        ForEach(latest) { m in
                            NavigationLink(value: ArenaRoute.match(m.id)) { MatchRow(m: m) }.buttonStyle(.plain)
                            if m.id != latest.last?.id { Divider() }
                        }
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
            }
            .navigationTitle("Arena")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { AccountButton() } }
            .alert("Sign-in didn't work", isPresented: Binding(get: { arena.lastError != nil && path.isEmpty }, set: { if !$0 { arena.lastError = nil } })) {
                Button("Try again") { Task { await arena.signIn() } }
                Button("OK", role: .cancel) {}
            } message: { Text(arena.lastError ?? "") }
            .refreshable { await load() }
            .task { await load() }
            .onChange(of: ladder) {
                Task {
                    top = nil
                    do { top = try await arena.ladder(ladder, limit: 10).items; error = nil }
                    catch { top = []; self.error = "Couldn't load the ladder: \(error.localizedDescription)" }
                }
            }
            .onChange(of: arena.user?.id) { Task { await loadMine() } }
            .navigationDestination(for: ArenaRoute.self) { r in
                switch r {
                case .ladder(let l): LadderView(initial: l)
                case .model(let id): ArenaModelView(id: id)
                case .match(let id): ArenaMatchView(id: id)
                case .tournaments: TournamentsView()
                case .tournament(let id): TournamentView(id: id)
                case .myModels: MyModelsView()
                }
            }
        }
    }

    private func load() async {
        do {
            async let t = arena.ladder(ladder, limit: 10)
            async let l = arena.matches(limit: 8)
            async let f = arena.currentTournament(.open)
            (top, latest, featured) = (try await t.items, try await l, try? await f)
            error = nil
            _ = try? await arena.refreshMe()
            await loadMine()
        } catch {
            self.error = "Couldn't reach the Arena: \(error.localizedDescription)"
        }
    }

    /// The signed-in player's active model (one per account), for the card at the top.
    private func loadMine() async {
        guard arena.user != nil else { mine = nil; return }
        mine = (try? await arena.myModels())?.first { $0.status == "active" }
    }
}

struct YourModelCard: View {
    let m: ArenaModel
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: m.twin != nil ? "person.2.fill" : "cpu")
                .font(.title3).foregroundStyle(.tint)
                .frame(width: 48, height: 48).glassEffect(.regular, in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text("Your model").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(m.name).font(.headline).lineLimit(1)
                Text("\(m.games) games" + (m.ladder == .twins ? " · Twins ladder" : "")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Text(m.rating.map { String(format: "%.0f", $0) } ?? "–").font(.score(.title))
            Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(16)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

// MARK: rows and small pieces

struct ArenaErrorRow: View {
    let text: String
    let retry: () -> Void
    var body: some View {
        HStack {
            Label(text, systemImage: "wifi.exclamationmark").font(.footnote).foregroundStyle(Theme.bad)
            Spacer()
            Button("Retry", action: retry).font(.footnote.weight(.semibold))
        }
        .padding(14)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

struct OwnerChip: View {
    let owner: ArenaOwner
    var body: some View {
        HStack(spacing: 5) {
            if let u = owner.avatar {
                AsyncImage(url: u) { $0.resizable() } placeholder: { Color.secondary.opacity(0.2) }
                    .frame(width: 16, height: 16).clipShape(Circle())
            } else {
                Image(systemName: "house.fill").font(.caption2)
            }
            Text(owner.name).font(.caption).lineLimit(1)
        }
        .foregroundStyle(.secondary)
    }
}

struct LadderRow: View {
    let item: ArenaLadderPage.Item
    var body: some View {
        HStack(spacing: 12) {
            Text("\(item.rank)").font(.score(.headline)).foregroundStyle(item.rank <= 3 ? Theme.hot : .secondary).frame(width: 26)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(item.model.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                    if item.model.twin != nil { Image(systemName: "person.2.fill").font(.caption2).foregroundStyle(.tint).accessibilityLabel("Twin") }
                }
                OwnerChip(owner: item.model.owner)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 0) {
                Text(String(format: "%.0f%@", item.rating, item.provisional ? "?" : "")).font(.score(.title3))
                Text("\(item.wins)–\(item.losses)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

struct MatchRow: View {
    let m: ArenaMatch
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                side(m.a, won: m.winner == "a")
                side(m.b, won: m.winner == "b")
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                if m.status == "done" {
                    Text("\(m.gamesWon[safe: 0] ?? 0)–\(m.gamesWon[safe: 1] ?? 0)").font(.score(.headline))
                } else {
                    Text(m.status.capitalized).font(.caption.weight(.semibold)).foregroundStyle(Theme.good)
                }
                Text(m.kind.capitalized + (m.ladder == .twins ? " · Twins" : "")).font(.caption2).foregroundStyle(.secondary)
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private func side(_ x: ArenaModel, won: Bool) -> some View {
        HStack(spacing: 6) {
            Circle().fill(won ? Theme.good : Color.clear).frame(width: 6, height: 6).accessibilityHidden(true)
            Text(x.name).font(.subheadline.weight(won ? .bold : .regular)).lineLimit(1)
        }
    }
}

struct BuildBars: View {
    let build: ArenaBuild
    var body: some View {
        VStack(spacing: 8) {
            row("Power", build.power, "harder smashes")
            row("Speed", build.speed, "faster footwork")
            row("Reach", build.reach, "longer racquet reach")
            row("Accuracy", build.accuracy, "tighter shots")
        }
    }
    private func row(_ name: String, _ v: Int, _ hint: String) -> some View {
        HStack(spacing: 10) {
            Text(name).font(.subheadline).frame(width: 74, alignment: .leading)
            Gauge(value: Double(v), in: 0...10) { EmptyView() }.gaugeStyle(.accessoryLinearCapacity).tint(Color.accentColor)
            Text("\(v)").font(.score(.subheadline)).frame(width: 22, alignment: .trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(name) \(v) of 10, \(hint)")
    }
}

struct TournamentCard: View {
    let t: ArenaTournament
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: t.status == "done" ? "trophy.fill" : "trophy")
                .font(.title2).foregroundStyle(Theme.hot)
                .frame(width: 48, height: 48).glassEffect(.regular, in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text(t.ladder == .twins ? "Twins tournament" : "Weekly tournament").font(.headline)
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(16)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
    private var subtitle: String {
        let when = t.startsAt.arenaDate?.formatted(.dateTime.weekday(.wide).hour().minute()) ?? t.startsAt
        switch t.status {
        case "scheduled": return "Starts \(when): the top 8 with 20+ games"
        case "running": return "In progress now"
        case "skipped": return "Skipped: fewer than 8 qualified"
        default: return "Finished \(when)"
        }
    }
}

/// Signed in: the avatar with an account menu. Signed out: Sign in with GitHub.
struct AccountButton: View {
    @Environment(ArenaClient.self) private var arena
    var body: some View {
        if let u = arena.user {
            Menu {
                Text("Signed in as \(u.login)")
                Button(role: .destructive) { Task { await arena.signOut() } } label: { Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right") }
            } label: {
                AsyncImage(url: URL(string: u.avatarUrl)) { $0.resizable() } placeholder: { Image(systemName: "person.crop.circle") }
                    .frame(width: 28, height: 28).clipShape(Circle())
                    .accessibilityLabel("Account: \(u.login)")
            }
        } else {
            Button { Task { await arena.signIn() } } label: {
                if arena.signingIn { ProgressView() } else { Label("Sign in", systemImage: "person.crop.circle.badge.plus") }
            }
            .disabled(arena.signingIn)
        }
    }
}

// MARK: ladder

struct LadderView: View {
    @Environment(ArenaClient.self) private var arena
    @State var initial: ArenaLadder
    @State private var items: [ArenaLadderPage.Item]?
    @State private var error: String?

    var body: some View {
        List {
            Section {
                Picker("Ladder", selection: $initial) { ForEach(ArenaLadder.allCases) { Text($0.title).tag($0) } }
                    .pickerStyle(.segmented).listRowBackground(Color.clear).listRowInsets(EdgeInsets())
            } footer: {
                if initial == .twins {
                    Text("A twin is trained from a real player's racquet sessions. It joins after 10 matches once it plays like its profile (build matches, tempo within 0.75–1.33). Twins play twins and the house players.")
                }
            }
            if let error { Section { ArenaErrorRow(text: error) { Task { await load() } } } }
            Section {
                if let items {
                    if items.isEmpty { Text("No models on this ladder yet.").foregroundStyle(.secondary) }
                    ForEach(items) { it in NavigationLink(value: ArenaRoute.model(it.model.id)) { LadderRow(item: it) } }
                } else {
                    ProgressView()
                }
            }
        }
        .themedList()
        .navigationTitle(initial == .twins ? "Twins ladder" : "Ladder")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task(id: initial) { items = nil; await load() }
    }

    private func load() async {
        do { items = try await arena.ladder(initial, limit: 100).items; error = nil } catch { self.error = error.localizedDescription }
    }
}

// MARK: model

struct ArenaModelView: View {
    @Environment(ArenaClient.self) private var arena
    let id: Int
    @State private var m: ArenaModelDetail?
    @State private var matches: [ArenaMatch] = []
    @State private var error: String?

    var body: some View {
        Screen {
            if let m {
                VStack(alignment: .leading, spacing: 8) {
                    Text(m.name).font(.system(.largeTitle, design: .rounded, weight: .bold))
                    HStack(spacing: 8) {
                        OwnerChip(owner: m.owner)
                        StatusChip(text: m.status.capitalized, color: m.status == "active" ? Theme.good : m.status == "rejected" ? Theme.bad : .secondary)
                        if m.ladder == .twins { StatusChip(text: "Twins ladder", systemImage: "person.2.fill", color: Theme.good) }
                    }
                    if let d = m.description, !d.isEmpty { Text(d).font(.subheadline) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let r = m.rejectReasons, !r.isEmpty {
                    Card("Rejected by the checker", systemImage: "xmark.octagon") {
                        ForEach(r, id: \.self) { Text("• " + $0).font(.subheadline) }
                    }
                }
                Card(m.ladder == .twins ? "Twins rating" : "Rating", systemImage: "chart.line.uptrend.xyaxis") {
                    HStack(alignment: .firstTextBaseline) {
                        BigStat(value: m.rating.map { String(format: "%.0f", $0) } ?? "–", label: "\(m.games) games", style: .largeTitle)
                    }
                    if m.ratingHistory.count > 1 {
                        Chart(Array(m.ratingHistory.enumerated()), id: \.offset) { i, p in
                            LineMark(x: .value("Game", i + 1), y: .value("Rating", p.rating)).interpolationMethod(.monotone).foregroundStyle(Color.accentColor)
                        }
                        .chartYScale(domain: .automatic(includesZero: false))
                        .frame(height: 140)
                        .accessibilityLabel("Rating over the last \(m.ratingHistory.count) games")
                    }
                }
                Card("Build", systemImage: "slider.horizontal.3") {
                    BuildBars(build: m.build)
                    if m.twin != nil { Text("Build derived from the player's profile.").font(.footnote).foregroundStyle(.secondary) }
                }
                if let t = m.twin { ArenaTwinCard(twin: t) }
                Card("Recent matches", systemImage: "figure.badminton") {
                    if matches.isEmpty { Text("No matches yet. New models play 10 placement matches first.").font(.subheadline).foregroundStyle(.secondary) }
                    ForEach(matches) { x in
                        NavigationLink(value: ArenaRoute.match(x.id)) { MatchRow(m: x) }.buttonStyle(.plain)
                        if x.id != matches.last?.id { Divider() }
                    }
                }
            } else if let error {
                ArenaErrorRow(text: error) { Task { await load() } }
            } else {
                ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
            }
        }
        .navigationTitle(m?.name ?? "Model")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        do {
            async let a = arena.model(id)
            async let b = arena.matches(model: id, limit: 20)
            (m, matches) = (try await a, (try? await b) ?? [])
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct ArenaTwinCard: View {
    let twin: ArenaTwin
    var body: some View {
        let p = twin.profile, f = twin.fidelity
        let verdict = f.ok ? "Plays like its profile" : f.checkedAt == nil ? "Checking" : "Does not match its profile yet"
        Card("Twin", systemImage: "person.2.fill") {
            StatusChip(text: verdict, systemImage: f.ok ? "checkmark.seal.fill" : f.checkedAt == nil ? "clock" : "exclamationmark.triangle",
                       color: f.ok ? Theme.good : f.checkedAt == nil ? Theme.hot : Theme.bad)
            Text("Trained from a real player's racquet sessions. The profile is self-reported; the Arena checks only that the model plays like it.")
                .font(.footnote).foregroundStyle(.secondary)
            StatGrid(minimum: 130) {
                BigStat(value: String(format: "%.0f", p.speed.p50Kmh), unit: "km/h", label: "Typical swing", style: .title2)
                BigStat(value: String(format: "%.0f", p.speed.p90Kmh), unit: "km/h", label: "Hard swing", style: .title2)
                BigStat(value: String(format: "%.1f", p.tempo.medianGapS), unit: "s", label: "Between shots", style: .title2)
                BigStat(value: "\(p.source.swings)", label: "Swings, \(Int(p.source.minutes.rounded())) min", style: .title2)
            }
            if let r = f.tempoRatio {
                Text(abs(r - 1) < 0.05 ? "Plays at its profile's tempo." : String(format: "Plays %.2fx as %@ as its profile (pass 0.75–1.33).", r > 1 ? r : 1 / r, r > 1 ? "slow" : "fast"))
                    .font(.subheadline)
            }
            ForEach(f.reasons ?? [], id: \.self) { Text($0).font(.footnote).foregroundStyle(Theme.bad) }
        }
    }
}

// MARK: match

struct ArenaMatchView: View {
    @Environment(ArenaClient.self) private var arena
    @Environment(\.colorScheme) private var scheme
    let id: Int
    @State private var m: ArenaMatch?
    @State private var error: String?
    @State private var watching = false

    var body: some View {
        Screen {
            if let m {
                Card {
                    HStack(alignment: .top) {
                        player(m.a, won: m.winner == "a", games: m.gamesWon[safe: 0] ?? 0)
                        Text("vs").font(.subheadline).foregroundStyle(.secondary).padding(.top, 14)
                        player(m.b, won: m.winner == "b", games: m.gamesWon[safe: 1] ?? 0)
                    }
                    if !m.games.isEmpty {
                        Text(m.games.map { "\($0[safe: 0] ?? 0)–\($0[safe: 1] ?? 0)" }.joined(separator: "  ·  "))
                            .font(.subheadline.monospacedDigit()).frame(maxWidth: .infinity)
                    }
                    Text([m.kind.capitalized, m.ladder == .twins ? "Twins ladder" : nil,
                          m.finishedAt?.arenaDate?.formatted(date: .abbreviated, time: .shortened)].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity)
                }
                if m.status == "done" {
                    Button { watching = true } label: { Label("Watch in 3D", systemImage: "play.fill") }.buttonStyle(.primary)
                    Text("The 3D replay is a 10–30 MB download.").font(.caption).foregroundStyle(.secondary)
                }
                if let rallies = m.rallies, !rallies.isEmpty {
                    Card("Rallies", systemImage: "list.number") {
                        ForEach(Array(rallies.enumerated()), id: \.offset) { i, r in
                            HStack {
                                Text("G\(r.game)").font(.caption.weight(.bold)).foregroundStyle(.secondary).frame(width: 26, alignment: .leading)
                                Text("\(r.score[safe: 0] ?? 0)–\(r.score[safe: 1] ?? 0)").font(.subheadline.monospacedDigit()).frame(width: 52, alignment: .leading)
                                Text("\(r.winner == "a" ? m.a.name : m.b.name) · \(r.reason.replacingOccurrences(of: "_", with: " "))")
                                    .font(.subheadline).lineLimit(1)
                                Spacer(minLength: 4)
                                Text("\(r.shots) shots").font(.caption).foregroundStyle(.secondary)
                            }
                            if i < rallies.count - 1 { Divider() }
                        }
                    }
                }
            } else if let error {
                ArenaErrorRow(text: error) { Task { await load() } }
            } else {
                ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
            }
        }
        .navigationTitle("Match")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .fullScreenCover(isPresented: $watching) {
            ReplaySheet(url: arena.viewerURL(match: id, dark: scheme == .dark))
        }
    }

    private func player(_ x: ArenaModel, won: Bool, games: Int) -> some View {
        NavigationLink(value: ArenaRoute.model(x.id)) {
            VStack(spacing: 4) {
                Text("\(games)").font(.system(size: 48, weight: .heavy, design: .rounded).monospacedDigit())
                    .foregroundStyle(won ? Theme.good : .secondary)
                Text(x.name).font(.subheadline.weight(.semibold)).multilineTextAlignment(.center).lineLimit(2)
                OwnerChip(owner: x.owner)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
    }

    private func load() async {
        do { m = try await arena.match(id); error = nil } catch { self.error = error.localizedDescription }
    }
}

/// The site's own 3D viewer (/viewer/), full screen.
struct ReplaySheet: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        WebView(url: url)
            .ignoresSafeArea()
            .overlay(alignment: .topTrailing) {
                Button { dismiss() } label: { Image(systemName: "xmark").font(.headline).frame(width: 44, height: 44) }
                    .glassEffect(.regular.interactive(), in: .circle)
                    .padding(.trailing, 16).padding(.top, 8)
                    .accessibilityLabel("Close replay")
            }
            .background(Color.black)
    }
}

struct WebView: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> WKWebView {
        let c = WKWebViewConfiguration()
        c.allowsInlineMediaPlayback = true
        let w = WKWebView(frame: .zero, configuration: c)
        w.isOpaque = false
        w.backgroundColor = .black
        w.scrollView.contentInsetAdjustmentBehavior = .never
        w.load(URLRequest(url: url))
        return w
    }
    func updateUIView(_ w: WKWebView, context: Context) {}
}

// MARK: tournaments

struct TournamentsView: View {
    @Environment(ArenaClient.self) private var arena
    @State private var ladder: ArenaLadder = .open
    @State private var current: ArenaTournament?
    @State private var past: [ArenaTournament] = []
    @State private var loaded = false

    var body: some View {
        Screen {
            Picker("Ladder", selection: $ladder) { ForEach(ArenaLadder.allCases) { Text($0.title).tag($0) } }.pickerStyle(.segmented)
            Text(ladder == .twins
                 ? "Sundays at 15:00 (server time), after the weekly fidelity re-check: the top 8 twins with 20+ games, single elimination, best of 3. Ratings don't change."
                 : "Sundays at 18:00 (server time): the top 8 by rating with 20+ games, single elimination, best of 3. Ratings don't change.")
                .font(.subheadline).foregroundStyle(.secondary)
            if let current {
                NavigationLink(value: ArenaRoute.tournament(current.id)) { TournamentCard(t: current) }.buttonStyle(.plain)
            } else if loaded {
                Text("Nothing scheduled yet.").font(.subheadline).foregroundStyle(.secondary)
            }
            Card("Past tournaments", systemImage: "clock.arrow.circlepath") {
                let done = past.filter { $0.status == "done" }
                if done.isEmpty { Text("None yet.").font(.subheadline).foregroundStyle(.secondary) }
                ForEach(done) { t in
                    NavigationLink(value: ArenaRoute.tournament(t.id)) {
                        HStack {
                            Text(t.startsAt.arenaDate?.formatted(date: .abbreviated, time: .omitted) ?? t.startsAt)
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 4).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("Tournaments")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: ladder) {
            current = try? await arena.currentTournament(ladder)
            past = (try? await arena.tournaments(ladder)) ?? []
            loaded = true
        }
    }
}

struct TournamentView: View {
    @Environment(ArenaClient.self) private var arena
    let id: Int
    @State private var t: ArenaTournament?
    private let names = ["Quarterfinals", "Semifinals", "Final"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let t {
                    Text(t.startsAt.arenaDate?.formatted(date: .complete, time: .shortened) ?? t.startsAt).font(.subheadline).foregroundStyle(.secondary).padding(.horizontal, 16)
                    if let rounds = t.rounds, !rounds.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(alignment: .center, spacing: 14) {
                                ForEach(Array(rounds.enumerated()), id: \.offset) { i, r in
                                    VStack(alignment: .leading, spacing: 10) {
                                        Text(names[safe: i + (3 - rounds.count)] ?? "Round \(i + 1)").font(.headline)
                                        ForEach(Array(r.enumerated()), id: \.offset) { _, tie in TieView(tie: tie) }
                                    }
                                    .frame(width: 210)
                                }
                            }
                            .padding(.horizontal, 16)
                        }
                    } else {
                        Text(t.status == "skipped" ? "Skipped this week: fewer than 8 models qualified." : "The bracket is drawn when it starts.")
                            .foregroundStyle(.secondary).padding(.horizontal, 16)
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                }
            }
            .padding(.vertical, 12)
        }
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle(t?.ladder == .twins ? "Twins tournament" : "Tournament")
        .navigationBarTitleDisplayMode(.inline)
        .task { t = try? await arena.tournament(id) }
    }
}

struct TieView: View {
    let tie: ArenaTie
    var body: some View {
        let content = VStack(spacing: 0) {
            slot(tie.a, seed: tie.seedA, won: tie.winner == "a")
            Divider()
            slot(tie.b, seed: tie.seedB, won: tie.winner == "b")
        }
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        if let mid = tie.matchId {
            NavigationLink(value: ArenaRoute.match(mid)) { content }.buttonStyle(.plain)
        } else {
            content
        }
    }
    private func slot(_ m: ArenaModel?, seed: Int?, won: Bool) -> some View {
        HStack(spacing: 8) {
            Text(seed.map(String.init) ?? "–").font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 16)
            Text(m?.name ?? "TBD").font(.subheadline.weight(won ? .bold : .regular)).lineLimit(1).foregroundStyle(m == nil ? .secondary : .primary)
            Spacer(minLength: 0)
            if won { Image(systemName: "checkmark").font(.caption.weight(.bold)).foregroundStyle(Theme.good) }
        }
        .padding(.horizontal, 10).padding(.vertical, 9)
    }
}

// MARK: train & upload

struct MyModelsView: View {
    @Environment(ArenaClient.self) private var arena
    @State private var mine: [ArenaModel]?
    @State private var picking = false
    @State private var uploading = false
    @State private var checking: ArenaModelDetail?
    @State private var message: String?
    @State private var retireTarget: ArenaModel?

    var body: some View {
        Screen {
            Card("Train your own player", systemImage: "cpu") {
                Text("Train with the free kit on your computer (Python 3.10+), export a .safetensors file (about 300 KB), then upload it here. House Base is included to start from.")
                    .font(.subheadline)
                Text("pip install \"./arena/kit[train]\"\noso-arena train --init base --build 9,6,5,4 --minutes 60 --out runs/try1\noso-arena export runs/try1/best -o mymodel.safetensors --name \"My Model\"")
                    .font(.caption.monospaced()).textSelection(.enabled)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                Text("Have a pod? Summary > Your style exports my_style.json for a twin that plays like you.").font(.footnote).foregroundStyle(.secondary)
            }
            if arena.user == nil {
                Card {
                    Label("Sign in to upload", systemImage: "person.crop.circle.badge.plus").font(.headline)
                    Text("Arena accounts use GitHub. One active model per account; 5 uploads per day.").font(.subheadline).foregroundStyle(.secondary)
                    Button { Task { await arena.signIn(); await load() } } label: { Label("Sign in with GitHub", systemImage: "person.badge.key") }
                        .buttonStyle(.primary).disabled(arena.signingIn)
                    if let e = arena.lastError { Text(e).font(.footnote).foregroundStyle(Theme.bad) }
                }
            } else {
                Card("Upload", systemImage: "square.and.arrow.up") {
                    if let left = arena.me?.uploadsLeftToday { Text("\(left) upload\(left == 1 ? "" : "s") left today. A new upload retires your active model (its history stays).").font(.footnote).foregroundStyle(.secondary) }
                    Button { picking = true } label: {
                        if uploading { ProgressView() } else { Label("Choose a .safetensors file", systemImage: "doc.badge.plus") }
                    }
                    .buttonStyle(.primary).disabled(uploading || checking?.status == "pending")
                    if let c = checking { CheckResult(m: c) }
                    if let message { Text(message).font(.footnote).foregroundStyle(Theme.bad) }
                }
                Card("Your models", systemImage: "person.fill") {
                    if let mine {
                        if mine.isEmpty { Text("Nothing uploaded yet.").font(.subheadline).foregroundStyle(.secondary) }
                        ForEach(mine) { m in
                            HStack {
                                NavigationLink(value: ArenaRoute.model(m.id)) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(m.name).font(.subheadline.weight(.semibold))
                                        Text([m.status.capitalized, m.rating.map { String(format: "%.0f", $0) }, "\(m.games) games"].compactMap { $0 }.joined(separator: " · "))
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                if m.status == "active" {
                                    Button("Retire", role: .destructive) { retireTarget = m }.font(.footnote.weight(.semibold))
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .navigationTitle("Train & upload")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { AccountButton() } }
        .task { _ = try? await arena.refreshMe(); await load() }
        .onChange(of: arena.user?.id) { Task { await load() } }
        .refreshable { await load() }
        .fileImporter(isPresented: $picking, allowedContentTypes: [UTType(filenameExtension: "safetensors") ?? .data, .data]) { r in
            if case .success(let url) = r { Task { await upload(url) } }
        }
        .confirmationDialog("Retire \(retireTarget?.name ?? "")?", isPresented: Binding(get: { retireTarget != nil }, set: { if !$0 { retireTarget = nil } }), titleVisibility: .visible) {
            Button("Retire", role: .destructive) {
                if let m = retireTarget {
                    Task {
                        do { try await arena.retire(m.id); message = nil } catch { message = error.localizedDescription }
                        await load()
                    }
                }
            }
        } message: {
            Text("It stops playing. Its history stays on the ladder pages.")
        }
    }

    private func load() async {
        guard arena.user != nil else { mine = nil; return }
        mine = try? await arena.myModels()
    }

    private func upload(_ url: URL) async {
        uploading = true
        message = nil
        defer { uploading = false }
        do {
            let r = try await arena.upload(url)
            _ = try? await arena.refreshMe()
            // the server checks it in its sandbox: poll until it leaves pending
            for _ in 0..<60 {
                let d = try await arena.model(r.id)
                checking = d
                if d.status != "pending" { break }
                try await Task.sleep(for: .seconds(3))
            }
            await load()
        } catch {
            message = error.localizedDescription
        }
    }
}

struct CheckResult: View {
    let m: ArenaModelDetail
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch m.status {
            case "pending":
                HStack(spacing: 8) { ProgressView(); Text("Checking \(m.name)… this takes about a minute.").font(.subheadline) }
            case "active":
                Label("\(m.name) was accepted. It now plays its 10 placement matches.", systemImage: "checkmark.circle.fill").font(.subheadline).foregroundStyle(Theme.good)
            default:
                Label("The checker rejected it:", systemImage: "xmark.octagon.fill").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.bad)
                ForEach(m.rejectReasons ?? ["no reason given"], id: \.self) { Text("• " + $0).font(.footnote) }
            }
            if let t = m.twin {
                Text("This file carries a style profile: \(t.profile.source.swings) swings, \(Int(t.profile.source.minutes.rounded())) min. It plays the open ladder until it passes the twin check after 10 matches.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

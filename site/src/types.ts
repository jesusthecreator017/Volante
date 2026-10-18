// The API contract (oso-web-app-PROMPT.md section 6). Additive changes only: new optional fields, never renames.

export interface User {
  id: number;
  login: string;
  avatar_url: string;
}

export interface Me {
  user: User | null;
  csrf: string;
  uploads_left_today?: number;
}

/** A racquet pod linked to the signed-in account. `name` is a display name kept on the server, not the Bluetooth name. */
export interface Pod {
  id: string;
  /** short hardware id printed in the app, e.g. OSO-4C99 */
  hw_id: string;
  name: string;
  last_seen: string | null;
}

export interface Season {
  id: number;
  contract: string;
  kit_version: string;
  started_at: string;
  ended_at: string | null;
  active: boolean;
}

export interface Build {
  power: number;
  speed: number;
  reach: number;
  accuracy: number;
}

export type Owner = User | { house: true };
export type ModelStatus = "pending" | "active" | "rejected" | "retired" | "hidden";

/** Volante Twins (arena/handoff/PROMPT.md section 14, docs/twins-site.md). The profile is self-reported: shown as given. */
export interface StyleProfile {
  schema: "volante-style/1";
  created_at: string;
  source: { sessions: number; swings: number; minutes: number; pod_fw: string; tool: string };
  speed: { p50_kmh: number; p90_kmh: number; cv: number; clipped_frac: number };
  tempo: { median_gap_s: number; p25_gap_s: number; p75_gap_s: number };
  stroke_mix: Record<string, number> | null;
  side_mix: { fh: number; bh: number } | null;
  notes?: string | null;
}

export interface Twin {
  profile: StyleProfile;
  fidelity: { ok: boolean; tempo_ratio: number | null; mix_js: number | null; checked_at: string | null; reasons?: string[] };
}

export type LadderName = "open" | "twins";

export interface ModelSummary {
  id: number;
  name: string;
  owner: Owner;
  build: Build;
  status: ModelStatus;
  rating: number | null;
  games: number;
  /** absent on servers older than section 14: treat as "open" */
  ladder?: LadderName;
  /** absent on older servers, null for a model without a style profile */
  twin?: Twin | null;
}

export interface ModelDetail extends ModelSummary {
  description: string | null;
  created_at: string;
  retired_at: string | null;
  season_id: number;
  reject_reasons?: string[];
  rating_history: { t: string; rating: number; match_id: number }[];
}

export interface LadderItem {
  rank: number;
  model: ModelSummary;
  rating: number;
  provisional: boolean;
  games: number;
  wins: number;
  losses: number;
}

export interface Ladder {
  season: Season;
  ladder?: LadderName;
  total: number;
  items: LadderItem[];
}

export type MatchKind = "ladder" | "placement" | "tournament";
export type Side = "a" | "b";

export interface MatchSummary {
  id: number;
  kind: MatchKind;
  season_id: number;
  created_at: string;
  finished_at: string | null;
  status: "queued" | "running" | "done" | "void" | "error";
  a: ModelSummary;
  b: ModelSummary;
  games_won: [number, number];
  winner: Side | null;
  games: [number, number][];
  rating_delta: { a: number; b: number } | null;
}

export type RallyReason = "missed" | "out" | "net" | "serve" | "net_touch" | "ceiling" | "let";

export interface Rally {
  game: number;
  t_start: number;
  t_end: number;
  server: Side;
  winner: Side;
  reason: RallyReason;
  shots: number;
  score: [number, number];
}

export interface MatchDetail extends MatchSummary {
  seed: number;
  rallies: Rally[];
}

export interface MatchPage {
  items: MatchSummary[];
  next_before: number | null;
}

export interface Tie {
  seed_a: number;
  seed_b: number;
  a: ModelSummary | null;
  b: ModelSummary | null;
  match_id: number | null;
  winner: Side | null;
}

export interface Tournament {
  id: number;
  season_id: number;
  starts_at: string;
  status: "scheduled" | "running" | "done";
  ladder?: LadderName;
  /** rounds[0] = quarterfinals */
  rounds: Tie[][];
}

export type TournamentSummary = Pick<Tournament, "id" | "season_id" | "starts_at" | "status"> & Partial<Tournament>;

export const isHouse = (o: Owner): o is { house: true } => "house" in o && o.house === true;

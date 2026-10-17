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

export interface ModelSummary {
  id: number;
  name: string;
  owner: Owner;
  build: Build;
  status: ModelStatus;
  rating: number | null;
  games: number;
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
  /** rounds[0] = quarterfinals */
  rounds: Tie[][];
}

export type TournamentSummary = Pick<Tournament, "id" | "season_id" | "starts_at" | "status"> & Partial<Tournament>;

export const isHouse = (o: Owner): o is { house: true } => "house" in o && o.house === true;

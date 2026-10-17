// Arena rules the frontend needs (handoff section 3, 5). The server is the authority; these are for display and
// fast feedback only.

import type { Build } from "./types";

export const STATS = ["power", "speed", "reach", "accuracy"] as const;
export const STAT_LABEL: Record<(typeof STATS)[number], string> = { power: "Power", speed: "Speed", reach: "Reach", accuracy: "Accuracy" };
export const STAT_SHORT: Record<(typeof STATS)[number], string> = { power: "P", speed: "S", reach: "R", accuracy: "A" };
export const STAT_EFFECT: Record<(typeof STATS)[number], string> = {
  power: "top shot speed (smashes)",
  speed: "run speed and acceleration",
  reach: "how far they can get to the shuttle",
  accuracy: "how precisely shots land",
};
export const BUILD_TOTAL = 24;

export const PLAY_STYLES: { name: string; build: Build }[] = [
  { name: "Smasher", build: { power: 9, speed: 6, reach: 5, accuracy: 4 } },
  { name: "Wall", build: { power: 3, speed: 8, reach: 8, accuracy: 5 } },
  { name: "Net Dominator", build: { power: 3, speed: 6, reach: 7, accuracy: 8 } },
  { name: "Controller", build: { power: 4, speed: 6, reach: 5, accuracy: 9 } },
  { name: "Speedster", build: { power: 4, speed: 9, reach: 5, accuracy: 6 } },
  { name: "Counter-attacker", build: { power: 6, speed: 7, reach: 7, accuracy: 4 } },
  { name: "All-rounder", build: { power: 6, speed: 6, reach: 6, accuracy: 6 } },
];

/** Nearest preset by Euclidean distance over the four stats. A guess: the server stores no style. */
export function suggestStyle(b: Build): string {
  let best = PLAY_STYLES[0];
  let bestD = Infinity;
  for (const p of PLAY_STYLES) {
    const d = STATS.reduce((sum, k) => sum + (b[k] - p.build[k]) ** 2, 0);
    if (d < bestD) {
      bestD = d;
      best = p;
    }
  }
  return best.name;
}

export const MAX_UPLOAD_BYTES = 1_048_576;
export const UPLOADS_PER_DAY = 5;

/** Client-side check before uploading: extension and size only (never parse the model in the browser). */
export function checkUploadFile(f: { name: string; size: number }): string | null {
  if (!/\.safetensors$/i.test(f.name)) return "Choose a .safetensors file exported with the oso-arena kit.";
  if (f.size === 0) return "That file is empty.";
  if (f.size > MAX_UPLOAD_BYTES) return `That file is ${(f.size / 1024).toFixed(0)} KB; the limit is 1 MiB (models are about 300 KB).`;
  return null;
}

export const PROVISIONAL_GAMES = 20;
export const TOURNAMENT_MIN_GAMES = 20;

export const RALLY_REASON: Record<string, string> = {
  missed: "missed",
  out: "out",
  net: "into the net",
  serve: "service fault",
  net_touch: "touched the net",
  ceiling: "hit the ceiling",
  let: "let",
};

// User settings, kept in localStorage (per device). Wrapped in try/catch: storage can be unavailable.

import { DEFAULT_ANALYSIS, type AnalysisSettings } from "./core/analysis";

export type SpeedUnit = "m/s" | "km/h" | "mph";

export interface Settings {
  handedness: "right" | "left";
  lengthM: number;
  headCenterM: number;
  speedUnit: SpeedUnit;
  startDps: number;
  endDps: number;
  endHoldMs: number;
  minDurationMs: number;
  beta: number;
  rallyGapS: number;
  mockEnabled: boolean;
  mockLossPct: number;
  mockJitterMs: number;
  /** Pi server base URL; "" = same origin when served by the Pi, disabled in the apps */
  serverUrl: string;
}

export const DEFAULT_SETTINGS: Settings = {
  handedness: "right",
  lengthM: 0.675,
  headCenterM: 0.53,
  speedUnit: "km/h",
  startDps: 300,
  endDps: 120,
  endHoldMs: 80,
  minDurationMs: 80,
  beta: 0.04,
  rallyGapS: 4,
  mockEnabled: true,
  mockLossPct: 0,
  mockJitterMs: 8,
  serverUrl: "",
};

const KEY = "oso.settings.v1";

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    /* storage unavailable */
  }
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(s: Settings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

export function analysisSettings(s: Settings): AnalysisSettings {
  return {
    ...DEFAULT_ANALYSIS,
    beta: s.beta,
    thresholds: { startDps: s.startDps, endDps: s.endDps, endHoldMs: s.endHoldMs, minDurationMs: s.minDurationMs },
    geometry: { lengthM: s.lengthM, headCenterM: s.headCenterM },
    handedness: s.handedness,
    rallyGapS: s.rallyGapS,
  };
}

export function speed(mps: number, unit: SpeedUnit): number {
  return unit === "m/s" ? mps : unit === "km/h" ? mps * 3.6 : mps * 2.236936;
}

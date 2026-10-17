// Exports: swings CSV, session summary JSON. (imu.bin is written by packet.encode / LiveSession.toImuBin.)

import type { SessionAnalysis } from "./analysis";
import type { Swing } from "./swings";

export const SWING_CSV_COLUMNS: [string, (s: Swing, label?: string) => string | number | null | boolean][] = [
  ["index", (s) => s.index],
  ["start_s", (s) => s.start.toFixed(3)],
  ["t_dev_start_s", (s) => s.tStart.toFixed(6)],
  ["duration_ms", (s) => s.durationMs.toFixed(1)],
  ["backswing_ms", (s) => s.backswingMs?.toFixed(1) ?? ""],
  ["forward_ms", (s) => s.forwardMs?.toFixed(1) ?? ""],
  ["follow_through_ms", (s) => s.followMs?.toFixed(1) ?? ""],
  ["peak_omega_dps", (s) => s.peakDps.toFixed(1)],
  ["peak_omega_rad_s", (s) => s.peakRad.toFixed(3)],
  ["fwd_peak_omega_dps", (s) => s.fwdPeakDps.toFixed(1)],
  ["head_speed_m_s_rotation_only", (s) => s.headSpeed.toFixed(2)],
  ["head_speed_km_h_rotation_only", (s) => (s.headSpeed * 3.6).toFixed(1)],
  ["peak_acc_g", (s) => s.peakAccG.toFixed(2)],
  ["impact", (s) => s.impact],
  ["impact_g", (s) => s.impactG?.toFixed(2) ?? ""],
  ["impact_vs_peak_ms", (s) => s.impactVsPeakMs?.toFixed(1) ?? ""],
  ["timing", (s) => s.timing ?? ""],
  ["handle_tilt_deg", (s) => s.handleTiltDeg?.toFixed(1) ?? ""],
  ["face_angle_deg", (s) => s.faceAngleDeg?.toFixed(1) ?? ""],
  ["overhead", (s) => s.overhead ?? ""],
  ["side_estimate", (s) => s.side ?? ""],
  ["stroke_heuristic", (s) => s.stroke],
  ["saturated_gyro_samples", (s) => s.saturatedSamples],
  ["saturated_acc_samples", (s) => s.accSaturatedSamples],
  ["lost_packets", (s) => s.lostPackets],
  ["confidence", (s) => s.confidence.toFixed(2)],
  ["label", (_s, label) => label ?? ""],
];

const csvCell = (v: unknown) => {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function swingsCsv(swings: Swing[], labels: Record<number, string> = {}): string {
  const head = SWING_CSV_COLUMNS.map((c) => c[0]).join(",");
  const rows = swings.map((s) => SWING_CSV_COLUMNS.map(([, f]) => csvCell(f(s, labels[s.index]))).join(","));
  return [head, ...rows].join("\n") + "\n";
}

export function summaryJson(a: SessionAnalysis, meta: Record<string, unknown> = {}): string {
  const c = a.calibration;
  return JSON.stringify(
    {
      format: "oso-session-summary",
      version: 1,
      ...meta,
      rate_hz: a.rateHz,
      measured_rate_hz: a.measuredRateHz,
      device_time_start_s: a.t0,
      device_time_end_s: a.t1,
      packets: a.capture.stats,
      gaps: a.capture.gaps.length,
      calibration: c.bias
        ? {
            gyro_bias_dps: c.bias.map((b) => (b * 180) / Math.PI),
            window_device_s: c.windowT,
            acc_rest_g: c.accRestG,
            gyro_noise_dps: c.gyroNoiseDps,
            acc_noise_g: c.accNoiseG,
            rest_tilt_deg: c.restTilt,
          }
        : null,
      clock: a.clock,
      sync_events: a.syncEvents,
      summary: a.summary,
      swings: a.swings.map(({ qAt, ...s }) => ({ ...s, q_at_xyzw: qAt })),
      notes: [
        "head_speed is rotation-only (|omega| x head-center distance): excludes arm translation, underestimates",
        "peak omega / head speed are lower bounds on swings with saturated gyro samples",
        "stroke type, side and timing are heuristics until a trained classifier exists",
        "yaw drifts with a 6-axis IMU; only tilt is absolute",
      ],
    },
    null,
    2,
  );
}

/**
 * Labeled training rows for a future stroke classifier: one row per labeled swing with the raw 6-axis window
 * (sensor counts converted to SI) from 300 ms before to 200 ms after the impact (or forward peak), plus features.
 */
export function labeledWindowsJson(a: SessionAnalysis, labels: Record<number, string>, sessionId: string): string {
  const rows = a.swings
    .filter((s) => labels[s.index])
    .map((s) => {
      const tc = s.tImpact ?? s.tFwdPeak;
      const win = a.samples.filter((x) => x.t >= tc - 0.3 && x.t <= tc + 0.2);
      const { qAt, ...features } = s;
      return {
        session: sessionId,
        swing: s.index,
        label: labels[s.index],
        center_t_dev_s: tc,
        features,
        q_at_xyzw: qAt,
        window: {
          t_s: win.map((x) => +(x.t - tc).toFixed(6)),
          acc_m_s2: win.map((x) => x.acc),
          gyro_rad_s: win.map((x) => x.gyro),
          saturated: win.map((x) => x.saturated),
        },
      };
    });
  return JSON.stringify({ format: "oso-labeled-swings", version: 1, rows });
}

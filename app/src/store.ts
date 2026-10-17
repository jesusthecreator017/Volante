// Local session storage (IndexedDB). Works in browsers and in the Capacitor WebViews. No accounts, no cloud.

import type { Marker } from "./core/packet";

export interface StoredSession {
  id: string;
  /** host time ms when recorded / imported */
  createdAt: number;
  name: string;
  source: "ble" | "mock" | "import" | "device-upload" | "sample" | "server";
  deviceName: string | null;
  /** imu.bin exactly as received (arrival order, markers excluded) */
  imuBin: Uint8Array;
  /** device sync markers (status events: pod tap or control 0x06), device time s */
  markers: Marker[];
  /** [t_dev newest sample, host s] per notification, for the clock fit (live streams only) */
  arrivals: [number, number][];
  /** user stroke labels, by swing index */
  labels: Record<number, string>;
  /** replay.bin from the video pipeline, if attached */
  replay: Uint8Array | null;
  /** cached headline numbers for the History list */
  headline: { durationS: number; swings: number; hits: number; maxSpeed: number | null; peakDps: number | null } | null;
  /** whether the session has been uploaded to the Pi server */
  uploadedAt?: number | null;
}

const DB = "oso";
const STORE = "sessions";

function open(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "id" });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, f: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((res, rej) => {
    const t = db.transaction(STORE, mode);
    const r = f(t.objectStore(STORE));
    t.oncomplete = () => res(r.result);
    t.onerror = () => rej(t.error);
  });
}

export const putSession = (s: StoredSession) => tx("readwrite", (st) => st.put(s));
export const getSession = (id: string) => tx<StoredSession | undefined>("readonly", (st) => st.get(id));
export const deleteSession = (id: string) => tx("readwrite", (st) => st.delete(id));
export async function listSessions(): Promise<StoredSession[]> {
  const all = await tx<StoredSession[]>("readonly", (st) => st.getAll());
  return all.sort((a, b) => b.createdAt - a.createdAt);
}

export const newId = () => new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14) + "-" + Math.random().toString(36).slice(2, 6);

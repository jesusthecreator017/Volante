// The embedded Arena 3D viewer (bundle shipped from web/arena, served at /viewer/). Shared by the match page, the model page and the
// landing teaser. Replays are 10-30 MB, so phones / data saver / cellular (and the teaser) load only after a tap.

import { replayUrl } from "./api";
import { h } from "./dom";

export const viewerSrc = (matchId: number) => `/viewer/index.html?match=${encodeURIComponent(replayUrl(matchId))}`;

interface ViewerWindow extends Window {
  oso_seek?: (t: number) => void;
}

/** True when the replay shouldn't start downloading on its own. */
export function metered(): boolean {
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean; type?: string } }).connection;
  return matchMedia("(max-width: 700px)").matches || !!conn?.saveData || conn?.type === "cellular";
}

export interface Mounted {
  /** Create the iframe if it isn't there yet. */
  mount: () => HTMLIFrameElement;
  /** Jump the viewer to replay time `t` (mounts first; waits for the iframe to load). */
  seek: (t: number) => void;
}

/** Fills `box` with the viewer for `m`: at once, or behind a "Load 3D replay" button when `onTap` (default: `metered()`). */
export function mountViewer(box: HTMLElement, m: { id: number; a: { name: string }; b: { name: string } }, onTap: boolean = metered(), note = "The 3D replay is a 10–30 MB download."): Mounted {
  let frame: HTMLIFrameElement | null = null;
  const mount = () => {
    if (!frame) {
      frame = h("iframe", { src: viewerSrc(m.id), title: `3D replay: ${m.a.name} vs ${m.b.name}`, allow: "fullscreen", class: "viewer-frame" });
      box.replaceChildren(frame);
    }
    return frame;
  };
  if (onTap) {
    box.append(h("div", { class: "viewer-wait" }, h("p", null, note), h("button", { type: "button", class: "btn primary", onclick: mount }, "Load 3D replay")));
  } else mount();
  const seek = (t: number) => {
    const f = mount();
    try {
      const w = f.contentWindow as ViewerWindow | null;
      if (w?.oso_seek) w.oso_seek(t);
      else f.addEventListener("load", () => (f.contentWindow as ViewerWindow | null)?.oso_seek?.(t), { once: true });
    } catch {
      // cross-origin viewer: seeking unavailable
    }
  };
  return { mount, seek };
}

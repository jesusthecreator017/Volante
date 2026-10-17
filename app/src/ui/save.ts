// Save/share a generated file: browser download on the web, share sheet in the native apps
// (a WebView can't download from a blob link).

import { Capacitor } from "@capacitor/core";
import { downloadBlob } from "./fmt";

function toBase64(u8: Uint8Array): string {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
  return btoa(s);
}

export async function saveFile(name: string, data: Uint8Array | string, mime: string) {
  if (!Capacitor.isNativePlatform()) {
    downloadBlob(name, data as BlobPart, mime);
    return;
  }
  const { Filesystem, Directory } = await import("@capacitor/filesystem");
  const { Share } = await import("@capacitor/share");
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const r = await Filesystem.writeFile({ path: name, data: toBase64(bytes), directory: Directory.Cache });
  await Share.share({ title: name, url: r.uri });
}

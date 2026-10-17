//! Asset bytes: from disk natively, over HTTP on web. Paths are relative to `assets/`.

/// `--replay FILE` (screenshot mode): read this file instead of assets/replay/replay.bin
#[cfg(not(target_arch = "wasm32"))]
pub static REPLAY_OVERRIDE: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();

#[cfg(not(target_arch = "wasm32"))]
pub async fn load_bytes(path: &str) -> anyhow::Result<Vec<u8>> {
    let mut full = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../assets")
        .join(path);
    if let (Some(over), "replay/replay.bin") = (REPLAY_OVERRIDE.get(), path) {
        full = over.clone();
    }
    std::fs::read(&full).map_err(|e| anyhow::anyhow!("reading {}: {e}", full.display()))
}

#[cfg(target_arch = "wasm32")]
pub async fn load_bytes(path: &str) -> anyhow::Result<Vec<u8>> {
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;

    let js_err = |e: wasm_bindgen::JsValue| anyhow::anyhow!("fetching {path}: {e:?}");
    let window = web_sys::window().ok_or_else(|| anyhow::anyhow!("no window"))?;
    let resp = JsFuture::from(window.fetch_with_str(&format!("assets/{path}")))
        .await
        .map_err(js_err)?;
    let resp: web_sys::Response = resp.dyn_into().map_err(js_err)?;
    if !resp.ok() {
        anyhow::bail!("fetching {path}: HTTP {}", resp.status());
    }
    let buf = JsFuture::from(resp.array_buffer().map_err(js_err)?)
        .await
        .map_err(js_err)?;
    Ok(js_sys::Uint8Array::new(&buf).to_vec())
}

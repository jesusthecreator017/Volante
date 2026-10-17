//! Asset bytes: from disk natively, over HTTP on web. Paths are relative to `assets/`.
//! The replay is the exception on web: the host page may hand it over as `globalThis.oso_replay`.

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

/// The replay bytes. Natively this is `replay/replay.bin` (or the `--replay` override).
#[cfg(not(target_arch = "wasm32"))]
pub async fn load_replay() -> anyhow::Result<Vec<u8>> {
    load_bytes("replay/replay.bin").await
}

/// The replay bytes. A host page can set `globalThis.oso_replay` to a promise (or value) of a
/// `Uint8Array`/`ArrayBuffer`; without it the bundled `replay/replay.bin` is fetched.
#[cfg(target_arch = "wasm32")]
pub async fn load_replay() -> anyhow::Result<Vec<u8>> {
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;

    let supplied = js_sys::Reflect::get(&js_sys::global(), &"oso_replay".into())
        .map_err(|e| anyhow::anyhow!("reading oso_replay: {e:?}"))?;
    if supplied.is_undefined() || supplied.is_null() {
        return load_bytes("replay/replay.bin").await;
    }
    let value = JsFuture::from(js_sys::Promise::resolve(&supplied))
        .await
        .map_err(|e| anyhow::anyhow!("replay promise rejected: {}", js_message(&e)))?;
    if let Some(bytes) = value.dyn_ref::<js_sys::Uint8Array>() {
        Ok(bytes.to_vec())
    } else if value.is_instance_of::<js_sys::ArrayBuffer>() {
        Ok(js_sys::Uint8Array::new(&value).to_vec())
    } else {
        anyhow::bail!("replay promise resolved to neither Uint8Array nor ArrayBuffer")
    }
}

/// Readable text for a JS rejection: an Error's message, a string as is, else its debug form.
#[cfg(target_arch = "wasm32")]
fn js_message(e: &wasm_bindgen::JsValue) -> String {
    use wasm_bindgen::JsCast;
    if let Some(err) = e.dyn_ref::<js_sys::Error>() {
        String::from(err.message())
    } else if let Some(s) = e.as_string() {
        s
    } else {
        format!("{e:?}")
    }
}

#[cfg(all(test, not(target_arch = "wasm32")))]
mod tests {
    use super::*;

    #[test]
    fn load_replay_reads_bundled_replay_natively() {
        let bytes = pollster::block_on(load_replay()).expect("bundled replay");
        assert_eq!(bytes, include_bytes!("../../../assets/replay/replay.bin"));
        assert!(crate::replay::Replay::parse(&bytes).is_ok());
    }
}

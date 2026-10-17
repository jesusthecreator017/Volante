/* tslint:disable */
/* eslint-disable */

/**
 * What the timeline UI polls each animation frame.
 */
export class Status {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    big_shuttle: boolean;
    cameras: number;
    duration: number;
    fps: number;
    frame_count: number;
    frame: number;
    has_shuttle: boolean;
    loaded: boolean;
    playing: boolean;
    skeleton: boolean;
    speed: number;
    time: number;
}

export function oso_preset(n: number): void;

export function oso_seek(t: number): void;

export function oso_set_playing(playing: boolean): void;

export function oso_set_speed(s: number): void;

/**
 * Call `.free()` on the result when done (JS side holds a wasm object).
 */
export function oso_status(): Status;

export function oso_step(n: number): void;

export function oso_toggle_big_shuttle(): void;

export function oso_toggle_play(): void;

export function oso_toggle_skeleton(): void;

export function run_web(): void;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_get_status_big_shuttle: (a: number) => number;
    readonly __wbg_get_status_cameras: (a: number) => number;
    readonly __wbg_get_status_duration: (a: number) => number;
    readonly __wbg_get_status_fps: (a: number) => number;
    readonly __wbg_get_status_frame: (a: number) => number;
    readonly __wbg_get_status_frame_count: (a: number) => number;
    readonly __wbg_get_status_has_shuttle: (a: number) => number;
    readonly __wbg_get_status_loaded: (a: number) => number;
    readonly __wbg_get_status_playing: (a: number) => number;
    readonly __wbg_get_status_skeleton: (a: number) => number;
    readonly __wbg_get_status_speed: (a: number) => number;
    readonly __wbg_get_status_time: (a: number) => number;
    readonly __wbg_set_status_big_shuttle: (a: number, b: number) => void;
    readonly __wbg_set_status_cameras: (a: number, b: number) => void;
    readonly __wbg_set_status_duration: (a: number, b: number) => void;
    readonly __wbg_set_status_fps: (a: number, b: number) => void;
    readonly __wbg_set_status_frame: (a: number, b: number) => void;
    readonly __wbg_set_status_frame_count: (a: number, b: number) => void;
    readonly __wbg_set_status_has_shuttle: (a: number, b: number) => void;
    readonly __wbg_set_status_loaded: (a: number, b: number) => void;
    readonly __wbg_set_status_playing: (a: number, b: number) => void;
    readonly __wbg_set_status_skeleton: (a: number, b: number) => void;
    readonly __wbg_set_status_speed: (a: number, b: number) => void;
    readonly __wbg_set_status_time: (a: number, b: number) => void;
    readonly __wbg_status_free: (a: number, b: number) => void;
    readonly oso_preset: (a: number) => void;
    readonly oso_seek: (a: number) => void;
    readonly oso_set_playing: (a: number) => void;
    readonly oso_set_speed: (a: number) => void;
    readonly oso_status: () => number;
    readonly oso_step: (a: number) => void;
    readonly oso_toggle_big_shuttle: () => void;
    readonly oso_toggle_play: () => void;
    readonly oso_toggle_skeleton: () => void;
    readonly run_web: () => void;
    readonly __wasm_bindgen_func_elem_10711: (a: number, b: number, c: number, d: number) => void;
    readonly __wasm_bindgen_func_elem_2455: (a: number, b: number, c: number, d: number) => void;
    readonly __wasm_bindgen_func_elem_2455_45: (a: number, b: number, c: number, d: number) => void;
    readonly __wasm_bindgen_func_elem_2455_46: (a: number, b: number, c: number, d: number) => void;
    readonly __wasm_bindgen_func_elem_8321: (a: number, b: number, c: number, d: number) => void;
    readonly __wasm_bindgen_func_elem_8320: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_37: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_38: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_39: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_41: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_42: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_43: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8320_44: (a: number, b: number, c: number) => void;
    readonly __wasm_bindgen_func_elem_8319: (a: number, b: number) => void;
    readonly __wbindgen_export: (a: number, b: number) => number;
    readonly __wbindgen_export2: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_export3: (a: number) => void;
    readonly __wbindgen_export4: (a: number, b: number, c: number) => void;
    readonly __wbindgen_export5: (a: number, b: number) => void;
    readonly __wbindgen_add_to_stack_pointer: (a: number) => number;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;

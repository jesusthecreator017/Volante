//! Match scoreboard: a single-sided board hung behind the far baseline (x = HALF_LENGTH + 3.0, z = 0), facing -x toward the
//! default camera (preset 1), on cables to the ceiling. 7-segment digits made of box instances (no text, no texture). Seen
//! from -x looking +x, screen-right is +z: P0 (orange, plays on -x) has the left pair of digits (z < 0), then a colon, then
//! P1 (cyan). Tens before units. A leading zero is hidden ("7", not "07").

use oso_render::scene::court::{CpuMesh, HALF_LENGTH, finish};
use oso_render::scene::hall::CEILING_Y;
use oso_render::scene::racquet::push_box;
use cgmath::Vector3;

pub const BOARD_X: f32 = HALF_LENGTH + 3.0;
pub const BOARD_SIZE: [f32; 3] = [0.1, 1.2, 3.0]; // x (thickness), y, z (width)
pub const BOARD_BOTTOM_Y: f32 = 3.5;
const CABLE_W: f32 = 0.04;
const CABLE_Z: f32 = 1.2;
/// Segment bars: thickness, lengths of the horizontal / vertical bars, depth in front of the board face
const T: f32 = 0.11;
const H_LEN: f32 = 0.42;
const V_LEN: f32 = 0.36;
const SEG_DEPTH: f32 = 0.05;
/// Digit cell: segment-centre spans (x: W, y: H)
const W: f32 = 0.46;
const H: f32 = 0.8;
pub const DOT_SIZE: f32 = 0.12;
pub const DOT_Y: [f32; 2] = [0.17, -0.17];

/// Screen-right position (u, = world z) of each digit cell `[P0 tens, P0 units, P1 tens, P1 units]`
pub const CELL_U: [f32; 4] = [-1.1, -0.5, 0.5, 1.1];

/// Bit i set = segment i lit; segments a (top), b (top right), c (bottom right), d (bottom), e (bottom left), f (top left), g (middle)
pub const SEGMENTS: [u8; 10] = [
    0b0111111, // 0: a b c d e f
    0b0000110, // 1: b c
    0b1011011, // 2: a b d e g
    0b1001111, // 3: a b c d g
    0b1100110, // 4: b c f g
    0b1101101, // 5: a c d f g
    0b1111101, // 6: a c d e f g
    0b0000111, // 7: a b c
    0b1111111, // 8
    0b1101111, // 9: a b c d f g
];

/// Digit of each of the 4 cells for the score `[p1, p2]`; the tens cell is None for scores below 10.
pub fn layout(score: [u32; 2]) -> [Option<u8>; 4] {
    let cell = |s: u32| {
        let s = s.min(99);
        [(s >= 10).then_some((s / 10) as u8), Some((s % 10) as u8)]
    };
    let [a, b] = cell(score[0]);
    let [c, d] = cell(score[1]);
    [a, b, c, d]
}

/// Lit segments (bitmask) of every cell
pub fn masks(score: [u32; 2]) -> [u8; 4] {
    layout(score).map(|d| d.map_or(0, |d| SEGMENTS[d as usize]))
}

/// Centre of segment `seg` (0..7) relative to its cell centre: (x, y) as seen from the cameras (x right, y up)
pub fn segment_offset(seg: usize) -> [f32; 2] {
    match seg {
        0 => [0.0, H / 2.0],
        1 => [W / 2.0, H / 4.0],
        2 => [W / 2.0, -H / 4.0],
        3 => [0.0, -H / 2.0],
        4 => [-W / 2.0, -H / 4.0],
        5 => [-W / 2.0, H / 4.0],
        _ => [0.0, 0.0],
    }
}

pub fn is_horizontal(seg: usize) -> bool {
    matches!(seg, 0 | 3 | 6)
}

pub fn board_centre_y() -> f32 {
    BOARD_BOTTOM_Y + BOARD_SIZE[1] / 2.0
}

/// World position of the point (u right = +z, y up from the board centre), `out` metres in front of the board face (toward -x)
pub fn world_pos(u: f32, y: f32, out: f32) -> Vector3<f32> {
    Vector3::new(BOARD_X - BOARD_SIZE[0] / 2.0 - out, board_centre_y() + y, u)
}

pub fn segment_world_pos(cell: usize, seg: usize) -> Vector3<f32> {
    let [dx, dy] = segment_offset(seg);
    world_pos(CELL_U[cell] + dx, dy, SEG_DEPTH / 2.0)
}

fn axes() -> [Vector3<f32>; 3] {
    [Vector3::unit_x(), Vector3::unit_y(), Vector3::unit_z()]
}

fn centred_box(size: [f32; 3]) -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    push_box(&mut mesh, Vector3::new(0.0, 0.0, 0.0), axes(), size);
    finish(mesh)
}

/// Horizontal segment bar, centred at the origin
pub fn bar_h() -> CpuMesh {
    centred_box([SEG_DEPTH, T, H_LEN])
}

/// Vertical segment bar, centred at the origin
pub fn bar_v() -> CpuMesh {
    centred_box([SEG_DEPTH, V_LEN, T])
}

/// Separator dot, centred at the origin
pub fn dot() -> CpuMesh {
    centred_box([SEG_DEPTH, DOT_SIZE, DOT_SIZE])
}

/// Dark board plus two hanging cables to the ceiling, in world coordinates
pub fn board() -> CpuMesh {
    let mut mesh = (Vec::new(), Vec::new());
    push_box(
        &mut mesh,
        Vector3::new(BOARD_X, board_centre_y(), 0.0),
        axes(),
        BOARD_SIZE,
    );
    let top = BOARD_BOTTOM_Y + BOARD_SIZE[1];
    for z in [-CABLE_Z, CABLE_Z] {
        push_box(
            &mut mesh,
            Vector3::new(BOARD_X, (top + CEILING_Y) / 2.0, z),
            axes(),
            [CABLE_W, CEILING_Y - top, CABLE_W],
        );
    }
    finish(mesh)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn segment_table_digits_0_to_9() {
        // Independent spelling of each digit as its lit segment letters
        let letters = [
            "abcdef", "bc", "abdeg", "abcdg", "bcfg", "acdfg", "acdefg", "abc", "abcdefg", "abcdfg",
        ];
        for (d, l) in letters.iter().enumerate() {
            let mask = l.bytes().fold(0u8, |m, c| m | 1 << (c - b'a'));
            assert_eq!(SEGMENTS[d], mask, "digit {d}");
        }
        assert_eq!(SEGMENTS[8], 0x7f);
        assert_eq!(SEGMENTS[1].count_ones(), 2);
    }

    #[test]
    fn score_layout_and_leading_zero() {
        assert_eq!(layout([0, 0]), [None, Some(0), None, Some(0)]);
        assert_eq!(layout([7, 15]), [None, Some(7), Some(1), Some(5)]);
        assert_eq!(layout([21, 9]), [Some(2), Some(1), None, Some(9)]);
        assert_eq!(layout([10, 20]), [Some(1), Some(0), Some(2), Some(0)]);
        let m = masks([7, 15]);
        assert_eq!(m, [0, SEGMENTS[7], SEGMENTS[1], SEGMENTS[5]]);
    }

    #[test]
    fn board_hangs_behind_the_far_baseline_inside_the_hall() {
        use oso_render::scene::hall::{X_MAX, Z_MAX, Z_MIN};
        let half = BOARD_SIZE[2] / 2.0;
        const { assert!(BOARD_BOTTOM_Y > 3.0 && BOARD_X > HALF_LENGTH + 2.0) };
        const { assert!(BOARD_X + BOARD_SIZE[0] / 2.0 < X_MAX) };
        const { assert!(BOARD_BOTTOM_Y + BOARD_SIZE[1] < CEILING_Y) };
        assert!(-half > Z_MIN && half < Z_MAX);
        for cell in 0..4 {
            for seg in 0..7 {
                let p = segment_world_pos(cell, seg);
                let ext = if is_horizontal(seg) {
                    H_LEN / 2.0
                } else {
                    T / 2.0
                };
                assert!(p.z.abs() + ext <= half, "{cell} {seg}");
                // In front of the face (toward -x), never inside the board
                assert!(p.x < BOARD_X - BOARD_SIZE[0] / 2.0);
                assert!((BOARD_BOTTOM_Y..=BOARD_BOTTOM_Y + BOARD_SIZE[1]).contains(&p.y));
            }
        }
    }

    #[test]
    #[allow(clippy::needless_range_loop)]
    fn each_player_digits_are_on_their_own_side() {
        // From -x looking +x screen-right is +z: P0 (orange) on the left (z < 0), P1 on the right; tens before units
        const { assert!(CELL_U[0] < 0.0 && CELL_U[1] < 0.0 && CELL_U[2] > 0.0 && CELL_U[3] > 0.0) };
        const { assert!(CELL_U[0] < CELL_U[1] && CELL_U[2] < CELL_U[3]) };
        for cell in 0..4 {
            assert_eq!(segment_world_pos(cell, 6).z, CELL_U[cell]);
        }
    }
}

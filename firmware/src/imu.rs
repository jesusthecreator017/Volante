//! MPU-9250 / 9255 / 6500 driver: 500 Hz accel + gyro through the 512 B FIFO (12 B per sample).

use esp_hal::Async;
use esp_hal::i2c::master::{Error, I2c};

const ADDR: u8 = 0x68;
const SMPLRT_DIV: u8 = 0x19;
const CONFIG: u8 = 0x1A;
const GYRO_CONFIG: u8 = 0x1B;
const ACCEL_CONFIG: u8 = 0x1C;
const ACCEL_CONFIG2: u8 = 0x1D;
const FIFO_EN: u8 = 0x23;
const USER_CTRL: u8 = 0x6A;
const PWR_MGMT_1: u8 = 0x6B;
const PWR_MGMT_2: u8 = 0x6C;
const FIFO_COUNT_H: u8 = 0x72;
const FIFO_R_W: u8 = 0x74;
const WHO_AM_I: u8 = 0x75;

pub const SAMPLE_BYTES: usize = 12;
/// The FIFO is 512 B; at 504 (42 samples) the next sample overwrote the oldest
pub const FIFO_FULL: u16 = 504;
/// (register, value) in the order they are written. Read back and logged at init.
const CONFIG_REGS: [(u8, u8); 7] = [
    (SMPLRT_DIV, 1),    // 1 kHz / 2 = 500 Hz
    (CONFIG, 2),        // gyro DLPF 92 Hz
    (GYRO_CONFIG, 0x18), // +-2000 dps
    (ACCEL_CONFIG, 0x18), // +-16 g
    (ACCEL_CONFIG2, 0x02), // accel DLPF 99 Hz
    (FIFO_EN, 0x78),    // accel + gyro
    (PWR_MGMT_2, 0),
];

pub struct Imu<'d> {
    i2c: I2c<'d, Async>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Chip {
    Mpu9250,
    Mpu9255,
    /// Common GY-9250 clone: same registers, no magnetometer
    Mpu6500,
}

impl<'d> Imu<'d> {
    pub fn new(i2c: I2c<'d, Async>) -> Self {
        Imu { i2c }
    }

    async fn write(&mut self, reg: u8, v: u8) -> Result<(), Error> {
        self.i2c.write_async(ADDR, &[reg, v]).await
    }

    async fn read(&mut self, reg: u8, buf: &mut [u8]) -> Result<(), Error> {
        self.i2c.write_read_async(ADDR, &[reg], buf).await
    }

    /// Diagnostic for a missing IMU: logs every address that ACKs
    pub async fn scan(&mut self) {
        let mut found = 0;
        for a in 0x08u8..0x78 {
            if self.i2c.write_async(a, &[]).await.is_ok() {
                log::info!("I2C device at {a:#04x}");
                found += 1;
            }
        }
        log::info!("I2C scan: {found} device(s)");
    }

    /// Resets and configures the chip. `Ok(None)` = a chip answered with an unknown WHO_AM_I.
    pub async fn init(&mut self) -> Result<Option<(Chip, u8)>, Error> {
        self.write(PWR_MGMT_1, 0x80).await?;
        embassy_time::Timer::after_millis(100).await;
        self.write(PWR_MGMT_1, 0x01).await?;
        embassy_time::Timer::after_millis(50).await;
        let mut id = [0u8];
        self.read(WHO_AM_I, &mut id).await?;
        log::info!("WHO_AM_I = {:#04x}", id[0]);
        let chip = match id[0] {
            0x71 => Chip::Mpu9250,
            0x73 => Chip::Mpu9255,
            0x70 => Chip::Mpu6500,
            _ => return Ok(None),
        };
        for (reg, v) in CONFIG_REGS {
            self.write(reg, v).await?;
        }
        self.reset_fifo().await?;
        for (reg, want) in CONFIG_REGS {
            let mut got = [0u8];
            self.read(reg, &mut got).await?;
            log::info!("reg {reg:#04x} = {:#04x} (wrote {want:#04x})", got[0]);
        }
        Ok(Some((chip, id[0])))
    }

    /// Sleep bit in PWR_MGMT_1: ~6 uA, persists while the 3V3 rail stays up (the next boot's init resets the chip)
    pub async fn sleep(&mut self) -> Result<(), Error> {
        self.write(USER_CTRL, 0x00).await?;
        self.write(PWR_MGMT_1, 0x40).await
    }

    /// FIFO reset (bit 2) while FIFO enable (bit 6) stays on
    pub async fn reset_fifo(&mut self) -> Result<(), Error> {
        self.write(USER_CTRL, 0x00).await?;
        self.write(USER_CTRL, 0x04).await?;
        self.write(USER_CTRL, 0x40).await
    }

    pub async fn fifo_count(&mut self) -> Result<u16, Error> {
        let mut c = [0u8; 2];
        self.read(FIFO_COUNT_H, &mut c).await?;
        Ok(u16::from_be_bytes(c) & 0x1FFF)
    }

    /// Whole samples out of the FIFO: `buf.len()` must be a multiple of 12
    pub async fn read_fifo(&mut self, buf: &mut [u8]) -> Result<(), Error> {
        self.read(FIFO_R_W, buf).await
    }
}

/// One FIFO sample (big-endian ax ay az gx gy gz) as native i16s
pub fn parse(s: &[u8]) -> [i16; 6] {
    core::array::from_fn(|i| i16::from_be_bytes([s[2 * i], s[2 * i + 1]]))
}

//! Guided stroke-dataset collection (`oso-rec collect`, Phase 16.1): the protocol, the block state machine and the
//! session folder's metadata. Pure (no BLE, no clock, no terminal), so the bin drives it and tests fake the time.

pub mod args;
pub mod blocks;
pub mod protocol;
pub mod session;

//! Data Collector Module
//!
//! High-speed data collection pipeline for Polymarket 15-minute markets.
//! Designed for continuous background operation with minimal Python overhead.
//!
//! Architecture:
//! - Background thread runs WebSocket connection (via polymarket SDK)
//! - All data stored to SQLite for replay
//! - Broadcast channel for real-time Python consumers
//!
//! Target: 500+ updates/second throughput

use chrono::Utc;
use pyo3::prelude::*;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::broadcast;

use crate::storage::DataStorage;

/// Market update from WebSocket or polling
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketUpdate {
    /// Timestamp in microseconds
    pub timestamp_us: i64,
    /// Market identifier
    pub market_id: String,
    /// Coin symbol
    pub coin: String,
    /// YES token ID
    pub yes_token_id: String,
    /// NO token ID
    pub no_token_id: String,
    /// YES best ask
    pub yes_ask: f64,
    /// YES best ask size
    pub yes_ask_size: f64,
    /// YES best bid
    pub yes_bid: f64,
    /// YES best bid size
    pub yes_bid_size: f64,
    /// NO best ask
    pub no_ask: f64,
    /// NO best ask size
    pub no_ask_size: f64,
    /// NO best bid
    pub no_bid: f64,
    /// NO best bid size
    pub no_bid_size: f64,
    /// Window end timestamp
    pub window_end_ts: i64,
    /// Seconds remaining
    pub seconds_remaining: f64,
}

impl MarketUpdate {
    /// Combined ask price
    pub fn combined_ask(&self) -> f64 {
        self.yes_ask + self.no_ask
    }

    /// Gross margin (1.0 - combined_ask)
    pub fn gross_margin(&self) -> f64 {
        1.0 - self.combined_ask()
    }

    /// Is this an arbitrage opportunity?
    pub fn is_opportunity(&self, min_margin: f64) -> bool {
        self.gross_margin() >= min_margin && self.yes_ask_size > 0.0 && self.no_ask_size > 0.0
    }
}

/// Collection statistics
#[derive(Debug, Clone, Default)]
pub struct CollectionStats {
    /// Total updates received
    pub updates_received: u64,
    /// Updates stored to database
    pub updates_stored: u64,
    /// Opportunities detected
    pub opportunities_detected: u64,
    /// Errors encountered
    pub errors: u64,
    /// Start time (Unix microseconds)
    pub started_at_us: i64,
    /// Last update time (Unix microseconds)
    pub last_update_us: i64,
}

impl CollectionStats {
    /// Updates per second
    pub fn updates_per_second(&self) -> f64 {
        let elapsed_us = self.last_update_us - self.started_at_us;
        if elapsed_us > 0 {
            (self.updates_received as f64) / (elapsed_us as f64 / 1_000_000.0)
        } else {
            0.0
        }
    }
}

/// Data collector configuration
#[derive(Debug, Clone)]
pub struct CollectorConfig {
    /// Database path
    pub db_path: String,
    /// Minimum margin to log as opportunity
    pub min_margin: f64,
    /// Store full orderbook JSON
    pub store_orderbook: bool,
    /// Broadcast channel capacity
    pub broadcast_capacity: usize,
}

impl Default for CollectorConfig {
    fn default() -> Self {
        Self {
            db_path: "data/gabagool_data.db".to_string(),
            min_margin: 0.005,
            store_orderbook: false,
            broadcast_capacity: 1000,
        }
    }
}

/// High-speed data collector
///
/// Provides a Python-callable interface for data collection.
/// Data is stored in SQLite and optionally broadcast to subscribers.
#[pyclass]
#[derive(Clone)]
pub struct DataCollector {
    /// SQLite storage
    storage: Arc<Mutex<DataStorage>>,
    /// Running flag
    running: Arc<AtomicBool>,
    /// Statistics
    updates_received: Arc<AtomicU64>,
    updates_stored: Arc<AtomicU64>,
    opportunities_detected: Arc<AtomicU64>,
    errors: Arc<AtomicU64>,
    started_at_us: Arc<AtomicU64>,
    last_update_us: Arc<AtomicU64>,
    /// Configuration
    min_margin: f64,
    store_orderbook: bool,
    /// Market cache for token ID lookups
    market_cache: Arc<Mutex<HashMap<String, (String, String)>>>,
}

#[pymethods]
impl DataCollector {
    /// Create new data collector
    #[new]
    #[pyo3(signature = (db_path="data/gabagool_data.db", min_margin=0.005, store_orderbook=false))]
    pub fn new(db_path: &str, min_margin: f64, store_orderbook: bool) -> PyResult<Self> {
        let storage = DataStorage::new(db_path)?;

        Ok(Self {
            storage: Arc::new(Mutex::new(storage)),
            running: Arc::new(AtomicBool::new(false)),
            updates_received: Arc::new(AtomicU64::new(0)),
            updates_stored: Arc::new(AtomicU64::new(0)),
            opportunities_detected: Arc::new(AtomicU64::new(0)),
            errors: Arc::new(AtomicU64::new(0)),
            started_at_us: Arc::new(AtomicU64::new(0)),
            last_update_us: Arc::new(AtomicU64::new(0)),
            min_margin,
            store_orderbook,
            market_cache: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    /// Start collection (sets running flag)
    pub fn start(&self) -> PyResult<()> {
        let now_us = Utc::now().timestamp_micros() as u64;
        self.started_at_us.store(now_us, Ordering::SeqCst);
        self.running.store(true, Ordering::SeqCst);
        Ok(())
    }

    /// Stop collection
    pub fn stop(&self) -> PyResult<()> {
        self.running.store(false, Ordering::SeqCst);
        Ok(())
    }

    /// Check if running
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Register a market for collection
    pub fn register_market(
        &self,
        market_id: &str,
        yes_token_id: &str,
        no_token_id: &str,
    ) -> PyResult<()> {
        let mut cache = self.market_cache.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        cache.insert(
            market_id.to_string(),
            (yes_token_id.to_string(), no_token_id.to_string()),
        );
        Ok(())
    }

    /// Process a market update (called from Python polling loop)
    ///
    /// This is the HOT PATH for data ingestion.
    /// Python fetches data, Rust processes and stores it.
    #[pyo3(signature = (market_id, coin, yes_token_id, no_token_id, yes_ask, yes_ask_size, yes_bid, yes_bid_size, no_ask, no_ask_size, no_bid, no_bid_size, window_end_ts, seconds_remaining, orderbook_json=None))]
    pub fn process_update(
        &self,
        market_id: &str,
        coin: &str,
        yes_token_id: &str,
        no_token_id: &str,
        yes_ask: f64,
        yes_ask_size: f64,
        yes_bid: f64,
        yes_bid_size: f64,
        no_ask: f64,
        no_ask_size: f64,
        no_bid: f64,
        no_bid_size: f64,
        window_end_ts: i64,
        seconds_remaining: f64,
        orderbook_json: Option<String>,
    ) -> PyResult<bool> {
        // Update stats
        self.updates_received.fetch_add(1, Ordering::Relaxed);
        let now_us = Utc::now().timestamp_micros() as u64;
        self.last_update_us.store(now_us, Ordering::Relaxed);

        // Check if this is an opportunity
        let combined_ask = yes_ask + no_ask;
        let gross_margin = 1.0 - combined_ask;
        let is_opportunity = gross_margin >= self.min_margin
            && yes_ask_size > 0.0
            && no_ask_size > 0.0;

        if is_opportunity {
            self.opportunities_detected.fetch_add(1, Ordering::Relaxed);
        }

        // Store to database
        let storage = self.storage.lock().map_err(|e| {
            self.errors.fetch_add(1, Ordering::Relaxed);
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        let ob_json = if self.store_orderbook {
            orderbook_json
        } else {
            None
        };

        storage.insert_snapshot(
            market_id,
            coin,
            yes_token_id,
            no_token_id,
            yes_ask,
            yes_ask_size,
            yes_bid,
            yes_bid_size,
            no_ask,
            no_ask_size,
            no_bid,
            no_bid_size,
            window_end_ts,
            seconds_remaining,
            ob_json,
        )?;

        self.updates_stored.fetch_add(1, Ordering::Relaxed);

        // If opportunity, also log to opportunities table
        if is_opportunity {
            let net_margin = gross_margin - 0.006; // Assume 2 * $0.003 gas
            let min_liquidity = (yes_ask_size * yes_ask).min(no_ask_size * no_ask);
            let max_position = min_liquidity * 2.0;
            let expected_profit = max_position * net_margin;

            storage.insert_opportunity(
                market_id,
                coin,
                yes_ask,
                no_ask,
                gross_margin,
                net_margin,
                yes_ask_size * yes_ask,
                no_ask_size * no_ask,
                max_position,
                expected_profit,
            )?;
        }

        Ok(is_opportunity)
    }

    /// Get collection statistics
    pub fn get_stats<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let dict = pyo3::types::PyDict::new(py);

        let updates_received = self.updates_received.load(Ordering::Relaxed);
        let updates_stored = self.updates_stored.load(Ordering::Relaxed);
        let opportunities = self.opportunities_detected.load(Ordering::Relaxed);
        let errors = self.errors.load(Ordering::Relaxed);
        let started_at = self.started_at_us.load(Ordering::Relaxed) as i64;
        let last_update = self.last_update_us.load(Ordering::Relaxed) as i64;

        dict.set_item("updates_received", updates_received)?;
        dict.set_item("updates_stored", updates_stored)?;
        dict.set_item("opportunities_detected", opportunities)?;
        dict.set_item("errors", errors)?;
        dict.set_item("started_at_us", started_at)?;
        dict.set_item("last_update_us", last_update)?;
        dict.set_item("running", self.running.load(Ordering::SeqCst))?;

        // Calculate updates per second
        let elapsed_us = last_update - started_at;
        let ups = if elapsed_us > 0 {
            (updates_received as f64) / (elapsed_us as f64 / 1_000_000.0)
        } else {
            0.0
        };
        dict.set_item("updates_per_second", ups)?;

        Ok(dict.into())
    }

    /// Get database statistics
    pub fn get_db_stats<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let storage = self.storage.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        storage.get_stats(py)
    }

    /// Get snapshots in time range (for replay)
    pub fn get_snapshots<'py>(
        &self,
        py: Python<'py>,
        start_timestamp_us: i64,
        end_timestamp_us: i64,
        limit: Option<i64>,
    ) -> PyResult<PyObject> {
        let storage = self.storage.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        storage.get_snapshots_in_range(py, start_timestamp_us, end_timestamp_us, limit)
    }

    /// Reset statistics (keeps data)
    pub fn reset_stats(&self) -> PyResult<()> {
        self.updates_received.store(0, Ordering::SeqCst);
        self.updates_stored.store(0, Ordering::SeqCst);
        self.opportunities_detected.store(0, Ordering::SeqCst);
        self.errors.store(0, Ordering::SeqCst);
        let now_us = Utc::now().timestamp_micros() as u64;
        self.started_at_us.store(now_us, Ordering::SeqCst);
        self.last_update_us.store(now_us, Ordering::SeqCst);
        Ok(())
    }
}

impl Default for DataCollector {
    fn default() -> Self {
        Self::new("data/gabagool_data.db", 0.005, false)
            .expect("Failed to create default collector")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_market_update() {
        let update = MarketUpdate {
            timestamp_us: 1000000,
            market_id: "test".to_string(),
            coin: "BTC".to_string(),
            yes_token_id: "yes".to_string(),
            no_token_id: "no".to_string(),
            yes_ask: 0.47,
            yes_ask_size: 50.0,
            yes_bid: 0.46,
            yes_bid_size: 30.0,
            no_ask: 0.48,
            no_ask_size: 40.0,
            no_bid: 0.47,
            no_bid_size: 25.0,
            window_end_ts: 1769540000,
            seconds_remaining: 300.0,
        };

        assert!((update.combined_ask() - 0.95).abs() < 0.001);
        assert!((update.gross_margin() - 0.05).abs() < 0.001);
        assert!(update.is_opportunity(0.01));
        assert!(!update.is_opportunity(0.10));
    }
}

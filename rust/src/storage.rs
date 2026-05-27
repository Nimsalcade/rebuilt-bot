//! SQLite Storage Module
//!
//! Provides persistent storage for market data with microsecond precision.
//! Designed for high-throughput writes and efficient replay queries.
//!
//! Schema:
//! - market_snapshots: Full orderbook snapshots with timestamps
//! - opportunities: Detected arbitrage opportunities
//! - paper_trades: Paper trade records from strategy evaluation
//!
//! Target: <5ms per write, batch inserts for higher throughput

use chrono::{DateTime, Utc};
use pyo3::prelude::*;
use rust_decimal::Decimal;
use rusqlite::{params, Connection, Result as SqliteResult};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::{Arc, Mutex};

/// Market snapshot record for storage
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketSnapshotRecord {
    /// Unique record ID (auto-generated)
    pub id: Option<i64>,
    /// Timestamp with microsecond precision (Unix microseconds)
    pub timestamp_us: i64,
    /// Market identifier (e.g., "btc-updown-15m-1769540000")
    pub market_id: String,
    /// Coin symbol (e.g., "BTC")
    pub coin: String,
    /// YES token ID
    pub yes_token_id: String,
    /// NO token ID
    pub no_token_id: String,
    /// YES best ask price
    pub yes_ask: Decimal,
    /// YES best ask size
    pub yes_ask_size: Decimal,
    /// YES best bid price
    pub yes_bid: Decimal,
    /// YES best bid size
    pub yes_bid_size: Decimal,
    /// NO best ask price
    pub no_ask: Decimal,
    /// NO best ask size
    pub no_ask_size: Decimal,
    /// NO best bid price
    pub no_bid: Decimal,
    /// NO best bid size
    pub no_bid_size: Decimal,
    /// Combined ask (YES + NO)
    pub combined_ask: Decimal,
    /// Combined bid (YES + NO)
    pub combined_bid: Decimal,
    /// Gross margin (1.0 - combined_ask)
    pub gross_margin: Decimal,
    /// Full orderbook JSON (optional, for deep analysis)
    pub orderbook_json: Option<String>,
    /// Window end timestamp (Unix seconds)
    pub window_end_ts: i64,
    /// Seconds remaining in window
    pub seconds_remaining: f64,
}

impl MarketSnapshotRecord {
    /// Create a new snapshot record with current timestamp
    pub fn new(
        market_id: &str,
        coin: &str,
        yes_token_id: &str,
        no_token_id: &str,
    ) -> Self {
        let now = Utc::now();
        Self {
            id: None,
            timestamp_us: now.timestamp_micros(),
            market_id: market_id.to_string(),
            coin: coin.to_string(),
            yes_token_id: yes_token_id.to_string(),
            no_token_id: no_token_id.to_string(),
            yes_ask: Decimal::ZERO,
            yes_ask_size: Decimal::ZERO,
            yes_bid: Decimal::ZERO,
            yes_bid_size: Decimal::ZERO,
            no_ask: Decimal::ZERO,
            no_ask_size: Decimal::ZERO,
            no_bid: Decimal::ZERO,
            no_bid_size: Decimal::ZERO,
            combined_ask: Decimal::ZERO,
            combined_bid: Decimal::ZERO,
            gross_margin: Decimal::ZERO,
            orderbook_json: None,
            window_end_ts: 0,
            seconds_remaining: 0.0,
        }
    }

    /// Set prices and calculate derived fields
    pub fn set_prices(
        &mut self,
        yes_ask: Decimal,
        yes_ask_size: Decimal,
        yes_bid: Decimal,
        yes_bid_size: Decimal,
        no_ask: Decimal,
        no_ask_size: Decimal,
        no_bid: Decimal,
        no_bid_size: Decimal,
    ) {
        self.yes_ask = yes_ask;
        self.yes_ask_size = yes_ask_size;
        self.yes_bid = yes_bid;
        self.yes_bid_size = yes_bid_size;
        self.no_ask = no_ask;
        self.no_ask_size = no_ask_size;
        self.no_bid = no_bid;
        self.no_bid_size = no_bid_size;
        self.combined_ask = yes_ask + no_ask;
        self.combined_bid = yes_bid + no_bid;
        self.gross_margin = Decimal::ONE - self.combined_ask;
    }
}

/// Detected opportunity record
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpportunityRecord {
    pub id: Option<i64>,
    pub timestamp_us: i64,
    pub market_id: String,
    pub coin: String,
    pub yes_ask: Decimal,
    pub no_ask: Decimal,
    pub combined_ask: Decimal,
    pub gross_margin: Decimal,
    pub net_margin: Decimal,
    pub yes_liquidity: Decimal,
    pub no_liquidity: Decimal,
    pub max_position_size: Decimal,
    pub expected_profit: Decimal,
}

/// Paper trade record
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperTradeRecord {
    pub id: Option<i64>,
    pub strategy_id: String,
    pub trade_id: String,
    pub timestamp_us: i64,
    pub market_id: String,
    pub coin: String,
    pub entry_combined_ask: Decimal,
    pub entry_margin: Decimal,
    pub trade_size: Decimal,
    pub tokens_acquired: Decimal,
    pub entry_cost: Decimal,
    pub gas_cost: Decimal,
    pub exit_timestamp_us: Option<i64>,
    pub payout: Option<Decimal>,
    pub gross_pnl: Option<Decimal>,
    pub net_pnl: Option<Decimal>,
    pub status: String,
}

/// SQLite storage manager
#[pyclass]
#[derive(Clone)]
pub struct DataStorage {
    conn: Arc<Mutex<Connection>>,
    db_path: String,
}

impl DataStorage {
    /// Create schema for all tables
    fn create_schema(conn: &Connection) -> SqliteResult<()> {
        // Market snapshots - core data collection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_us INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                yes_token_id TEXT NOT NULL,
                no_token_id TEXT NOT NULL,
                yes_ask TEXT NOT NULL,
                yes_ask_size TEXT NOT NULL,
                yes_bid TEXT NOT NULL,
                yes_bid_size TEXT NOT NULL,
                no_ask TEXT NOT NULL,
                no_ask_size TEXT NOT NULL,
                no_bid TEXT NOT NULL,
                no_bid_size TEXT NOT NULL,
                combined_ask TEXT NOT NULL,
                combined_bid TEXT NOT NULL,
                gross_margin TEXT NOT NULL,
                orderbook_json TEXT,
                window_end_ts INTEGER NOT NULL,
                seconds_remaining REAL NOT NULL
            )",
            [],
        )?;

        // Indexes for common queries
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON market_snapshots(timestamp_us)",
            [],
        )?;
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_market ON market_snapshots(market_id, timestamp_us)",
            [],
        )?;
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_coin ON market_snapshots(coin, timestamp_us)",
            [],
        )?;

        // Opportunities table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_us INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                yes_ask TEXT NOT NULL,
                no_ask TEXT NOT NULL,
                combined_ask TEXT NOT NULL,
                gross_margin TEXT NOT NULL,
                net_margin TEXT NOT NULL,
                yes_liquidity TEXT NOT NULL,
                no_liquidity TEXT NOT NULL,
                max_position_size TEXT NOT NULL,
                expected_profit TEXT NOT NULL
            )",
            [],
        )?;

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opportunities_timestamp ON opportunities(timestamp_us)",
            [],
        )?;

        // Paper trades table (for strategy evaluation)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                timestamp_us INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                entry_combined_ask TEXT NOT NULL,
                entry_margin TEXT NOT NULL,
                trade_size TEXT NOT NULL,
                tokens_acquired TEXT NOT NULL,
                entry_cost TEXT NOT NULL,
                gas_cost TEXT NOT NULL,
                exit_timestamp_us INTEGER,
                payout TEXT,
                gross_pnl TEXT,
                net_pnl TEXT,
                status TEXT NOT NULL
            )",
            [],
        )?;

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy_id, timestamp_us)",
            [],
        )?;

        // Strategy configurations table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS strategy_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT UNIQUE NOT NULL,
                yes_threshold TEXT NOT NULL,
                no_threshold TEXT NOT NULL,
                profit_threshold TEXT NOT NULL,
                max_trade_size TEXT NOT NULL,
                gas_cost TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                description TEXT
            )",
            [],
        )?;

        // Collection metadata
        conn.execute(
            "CREATE TABLE IF NOT EXISTS collection_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )",
            [],
        )?;

        Ok(())
    }
}

#[pymethods]
impl DataStorage {
    /// Create new storage instance
    #[new]
    #[pyo3(signature = (db_path="data/gabagool_data.db"))]
    pub fn new(db_path: &str) -> PyResult<Self> {
        // Create parent directories if needed
        if let Some(parent) = Path::new(db_path).parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                    "Failed to create directory: {}",
                    e
                ))
            })?;
        }

        let conn = Connection::open(db_path).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to open database: {}", e))
        })?;

        // Enable WAL mode for better concurrent performance
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA cache_size=10000;
             PRAGMA temp_store=MEMORY;",
        )
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to set pragmas: {}", e))
        })?;

        Self::create_schema(&conn).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to create schema: {}", e))
        })?;

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
            db_path: db_path.to_string(),
        })
    }

    /// Insert a market snapshot
    pub fn insert_snapshot(
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
    ) -> PyResult<i64> {
        let timestamp_us = Utc::now().timestamp_micros();
        let combined_ask = yes_ask + no_ask;
        let combined_bid = yes_bid + no_bid;
        let gross_margin = 1.0 - combined_ask;

        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        conn.execute(
            "INSERT INTO market_snapshots (
                timestamp_us, market_id, coin, yes_token_id, no_token_id,
                yes_ask, yes_ask_size, yes_bid, yes_bid_size,
                no_ask, no_ask_size, no_bid, no_bid_size,
                combined_ask, combined_bid, gross_margin,
                orderbook_json, window_end_ts, seconds_remaining
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
            params![
                timestamp_us,
                market_id,
                coin,
                yes_token_id,
                no_token_id,
                yes_ask.to_string(),
                yes_ask_size.to_string(),
                yes_bid.to_string(),
                yes_bid_size.to_string(),
                no_ask.to_string(),
                no_ask_size.to_string(),
                no_bid.to_string(),
                no_bid_size.to_string(),
                combined_ask.to_string(),
                combined_bid.to_string(),
                gross_margin.to_string(),
                orderbook_json,
                window_end_ts,
                seconds_remaining
            ],
        )
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Insert failed: {}", e))
        })?;

        Ok(conn.last_insert_rowid())
    }

    /// Insert an opportunity record
    pub fn insert_opportunity(
        &self,
        market_id: &str,
        coin: &str,
        yes_ask: f64,
        no_ask: f64,
        gross_margin: f64,
        net_margin: f64,
        yes_liquidity: f64,
        no_liquidity: f64,
        max_position_size: f64,
        expected_profit: f64,
    ) -> PyResult<i64> {
        let timestamp_us = Utc::now().timestamp_micros();
        let combined_ask = yes_ask + no_ask;

        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        conn.execute(
            "INSERT INTO opportunities (
                timestamp_us, market_id, coin, yes_ask, no_ask, combined_ask,
                gross_margin, net_margin, yes_liquidity, no_liquidity,
                max_position_size, expected_profit
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                timestamp_us,
                market_id,
                coin,
                yes_ask.to_string(),
                no_ask.to_string(),
                combined_ask.to_string(),
                gross_margin.to_string(),
                net_margin.to_string(),
                yes_liquidity.to_string(),
                no_liquidity.to_string(),
                max_position_size.to_string(),
                expected_profit.to_string()
            ],
        )
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Insert failed: {}", e))
        })?;

        Ok(conn.last_insert_rowid())
    }

    /// Get snapshot count
    pub fn snapshot_count(&self) -> PyResult<i64> {
        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM market_snapshots", [], |row| row.get(0))
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Query failed: {}", e))
            })?;

        Ok(count)
    }

    /// Get opportunity count
    pub fn opportunity_count(&self) -> PyResult<i64> {
        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM opportunities", [], |row| row.get(0))
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Query failed: {}", e))
            })?;

        Ok(count)
    }

    /// Get database file path
    pub fn get_db_path(&self) -> String {
        self.db_path.clone()
    }

    /// Get database size in bytes
    pub fn get_db_size(&self) -> PyResult<u64> {
        let metadata = std::fs::metadata(&self.db_path).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to get file size: {}", e))
        })?;
        Ok(metadata.len())
    }

    /// Vacuum database to reclaim space
    pub fn vacuum(&self) -> PyResult<()> {
        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        conn.execute("VACUUM", []).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Vacuum failed: {}", e))
        })?;

        Ok(())
    }

    /// Get snapshots in time range (for replay)
    pub fn get_snapshots_in_range<'py>(
        &self,
        py: Python<'py>,
        start_timestamp_us: i64,
        end_timestamp_us: i64,
        limit: Option<i64>,
    ) -> PyResult<PyObject> {
        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        let limit_clause = limit.map_or(String::new(), |l| format!(" LIMIT {}", l));

        let mut stmt = conn
            .prepare(&format!(
                "SELECT timestamp_us, market_id, coin, yes_ask, no_ask,
                        yes_ask_size, no_ask_size, combined_ask, gross_margin,
                        window_end_ts, seconds_remaining
                 FROM market_snapshots
                 WHERE timestamp_us >= ?1 AND timestamp_us <= ?2
                 ORDER BY timestamp_us ASC{}",
                limit_clause
            ))
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Prepare failed: {}", e))
            })?;

        let rows = stmt
            .query_map(params![start_timestamp_us, end_timestamp_us], |row| {
                Ok((
                    row.get::<_, i64>(0)?,      // timestamp_us
                    row.get::<_, String>(1)?,   // market_id
                    row.get::<_, String>(2)?,   // coin
                    row.get::<_, String>(3)?,   // yes_ask
                    row.get::<_, String>(4)?,   // no_ask
                    row.get::<_, String>(5)?,   // yes_ask_size
                    row.get::<_, String>(6)?,   // no_ask_size
                    row.get::<_, String>(7)?,   // combined_ask
                    row.get::<_, String>(8)?,   // gross_margin
                    row.get::<_, i64>(9)?,      // window_end_ts
                    row.get::<_, f64>(10)?,     // seconds_remaining
                ))
            })
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Query failed: {}", e))
            })?;

        let list = pyo3::types::PyList::empty(py);
        for row_result in rows {
            let row = row_result.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Row error: {}", e))
            })?;

            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("timestamp_us", row.0)?;
            dict.set_item("market_id", row.1)?;
            dict.set_item("coin", row.2)?;
            dict.set_item("yes_ask", row.3)?;
            dict.set_item("no_ask", row.4)?;
            dict.set_item("yes_ask_size", row.5)?;
            dict.set_item("no_ask_size", row.6)?;
            dict.set_item("combined_ask", row.7)?;
            dict.set_item("gross_margin", row.8)?;
            dict.set_item("window_end_ts", row.9)?;
            dict.set_item("seconds_remaining", row.10)?;
            list.append(dict)?;
        }

        Ok(list.into())
    }

    /// Get collection statistics
    pub fn get_stats<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let conn = self.conn.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;

        let snapshot_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM market_snapshots", [], |row| {
                row.get(0)
            })
            .unwrap_or(0);

        let opportunity_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM opportunities", [], |row| row.get(0))
            .unwrap_or(0);

        let (min_ts, max_ts): (Option<i64>, Option<i64>) = conn
            .query_row(
                "SELECT MIN(timestamp_us), MAX(timestamp_us) FROM market_snapshots",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap_or((None, None));

        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("snapshot_count", snapshot_count)?;
        dict.set_item("opportunity_count", opportunity_count)?;
        dict.set_item("min_timestamp_us", min_ts)?;
        dict.set_item("max_timestamp_us", max_ts)?;
        dict.set_item("db_path", &self.db_path)?;

        if let Ok(size) = std::fs::metadata(&self.db_path).map(|m| m.len()) {
            dict.set_item("db_size_bytes", size)?;
        }

        Ok(dict.into())
    }
}

impl Default for DataStorage {
    fn default() -> Self {
        Self::new("data/gabagool_data.db").expect("Failed to create default storage")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_storage_creation() {
        let test_path = "/tmp/test_gabagool.db";
        let _ = fs::remove_file(test_path); // Clean up from previous runs

        let storage = DataStorage::new(test_path).unwrap();
        assert_eq!(storage.snapshot_count().unwrap(), 0);
        assert_eq!(storage.opportunity_count().unwrap(), 0);

        fs::remove_file(test_path).ok();
    }

    #[test]
    fn test_insert_snapshot() {
        let test_path = "/tmp/test_gabagool_insert.db";
        let _ = fs::remove_file(test_path);

        let storage = DataStorage::new(test_path).unwrap();

        let id = storage
            .insert_snapshot(
                "btc-15min-test",
                "BTC",
                "yes_token_123",
                "no_token_456",
                0.47,
                50.0,
                0.46,
                30.0,
                0.48,
                40.0,
                0.47,
                25.0,
                1769540000,
                300.0,
                None,
            )
            .unwrap();

        assert!(id > 0);
        assert_eq!(storage.snapshot_count().unwrap(), 1);

        fs::remove_file(test_path).ok();
    }
}

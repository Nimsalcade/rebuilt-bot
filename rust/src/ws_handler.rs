//! WebSocket Handler Module
//!
//! Handles real-time price feeds from Polymarket RTDS (Real-Time Data Socket).
//! Subscribes to crypto price updates for BTC, ETH, SOL, XRP.
//!
//! Target: <10ms per message (vs 100-200ms Python)
//!
//! Note: This module provides a simplified interface for Python integration.
//! The actual WebSocket connection is managed by the Polymarket SDK.

use pyo3::prelude::*;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Price update from WebSocket feed
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PriceUpdate {
    /// Symbol (e.g., "BTCUSDT")
    pub symbol: String,
    /// Current price
    pub price: Decimal,
    /// Timestamp (milliseconds)
    pub timestamp: i64,
}

/// Market snapshot with order book data
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct MarketSnapshot {
    /// Market identifier
    pub market_id: String,
    /// Coin symbol
    pub coin: String,
    /// YES best ask price
    pub yes_ask: Decimal,
    /// YES best ask size (in dollars)
    pub yes_ask_size: Decimal,
    /// NO best ask price
    pub no_ask: Decimal,
    /// NO best ask size (in dollars)
    pub no_ask_size: Decimal,
    /// Combined price (yes_ask + no_ask)
    pub combined: Decimal,
    /// Timestamp
    pub timestamp: i64,
}

impl MarketSnapshot {
    /// Create from order book data
    pub fn from_books(
        market_id: &str,
        coin: &str,
        yes_asks: &[(Decimal, Decimal)], // (price, size)
        no_asks: &[(Decimal, Decimal)],
    ) -> Self {
        let (yes_ask, yes_ask_size) = yes_asks
            .first()
            .cloned()
            .unwrap_or((Decimal::ZERO, Decimal::ZERO));

        let (no_ask, no_ask_size) = no_asks
            .first()
            .cloned()
            .unwrap_or((Decimal::ZERO, Decimal::ZERO));

        let combined = yes_ask + no_ask;

        Self {
            market_id: market_id.to_string(),
            coin: coin.to_string(),
            yes_ask,
            yes_ask_size,
            no_ask,
            no_ask_size,
            combined,
            timestamp: chrono::Utc::now().timestamp_millis(),
        }
    }
}

/// WebSocket handler for price feeds
///
/// This is a simplified handler that caches the latest snapshots.
/// The actual WebSocket connection would be managed externally
/// (either by Python or a background Rust task).
#[pyclass]
#[derive(Clone)]
pub struct PriceFeedCache {
    /// Latest snapshots per market
    snapshots: Arc<RwLock<HashMap<String, MarketSnapshot>>>,
    /// Latest crypto prices (from Binance feed)
    crypto_prices: Arc<RwLock<HashMap<String, PriceUpdate>>>,
}

#[pymethods]
impl PriceFeedCache {
    /// Create new price feed cache
    #[new]
    pub fn new() -> Self {
        Self {
            snapshots: Arc::new(RwLock::new(HashMap::new())),
            crypto_prices: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Update market snapshot (called from Python when new data arrives)
    #[pyo3(signature = (market_id, coin, yes_ask, yes_ask_size, no_ask, no_ask_size))]
    pub fn update_snapshot<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        coin: &str,
        yes_ask: f64,
        yes_ask_size: f64,
        no_ask: f64,
        no_ask_size: f64,
    ) -> PyResult<()> {
        let snapshots = self.snapshots.clone();
        let snapshot = MarketSnapshot {
            market_id: market_id.to_string(),
            coin: coin.to_string(),
            yes_ask: Decimal::try_from(yes_ask).unwrap_or(Decimal::ZERO),
            yes_ask_size: Decimal::try_from(yes_ask_size).unwrap_or(Decimal::ZERO),
            no_ask: Decimal::try_from(no_ask).unwrap_or(Decimal::ZERO),
            no_ask_size: Decimal::try_from(no_ask_size).unwrap_or(Decimal::ZERO),
            combined: Decimal::try_from(yes_ask + no_ask).unwrap_or(Decimal::ZERO),
            timestamp: chrono::Utc::now().timestamp_millis(),
        };

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                snapshots
                    .write()
                    .await
                    .insert(market_id.to_string(), snapshot);
            });
        });

        Ok(())
    }

    /// Update crypto price (called from Python when Binance price updates)
    pub fn update_crypto_price<'py>(
        &self,
        py: Python<'py>,
        symbol: &str,
        price: f64,
    ) -> PyResult<()> {
        let crypto_prices = self.crypto_prices.clone();
        let update = PriceUpdate {
            symbol: symbol.to_string(),
            price: Decimal::try_from(price).unwrap_or(Decimal::ZERO),
            timestamp: chrono::Utc::now().timestamp_millis(),
        };

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                crypto_prices
                    .write()
                    .await
                    .insert(symbol.to_string(), update);
            });
        });

        Ok(())
    }

    /// Get latest snapshot for a market
    pub fn get_snapshot<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let snapshots = self.snapshots.clone();
        let market_id = market_id.to_string();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async { snapshots.read().await.get(&market_id).cloned() })
        });

        match result {
            Some(snap) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("market_id", &snap.market_id)?;
                dict.set_item("coin", &snap.coin)?;
                dict.set_item("yes_ask", snap.yes_ask.to_string())?;
                dict.set_item("yes_ask_size", snap.yes_ask_size.to_string())?;
                dict.set_item("no_ask", snap.no_ask.to_string())?;
                dict.set_item("no_ask_size", snap.no_ask_size.to_string())?;
                dict.set_item("combined", snap.combined.to_string())?;
                dict.set_item("timestamp", snap.timestamp)?;
                Ok(Some(dict.into()))
            }
            None => Ok(None),
        }
    }

    /// Get all market snapshots
    pub fn get_all_snapshots<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let snapshots = self.snapshots.clone();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async { snapshots.read().await.clone() })
        });

        let dict = pyo3::types::PyDict::new(py);
        for (market_id, snap) in result {
            let snap_dict = pyo3::types::PyDict::new(py);
            snap_dict.set_item("market_id", &snap.market_id)?;
            snap_dict.set_item("coin", &snap.coin)?;
            snap_dict.set_item("yes_ask", snap.yes_ask.to_string())?;
            snap_dict.set_item("yes_ask_size", snap.yes_ask_size.to_string())?;
            snap_dict.set_item("no_ask", snap.no_ask.to_string())?;
            snap_dict.set_item("no_ask_size", snap.no_ask_size.to_string())?;
            snap_dict.set_item("combined", snap.combined.to_string())?;
            snap_dict.set_item("timestamp", snap.timestamp)?;
            dict.set_item(market_id, snap_dict)?;
        }

        Ok(dict.into())
    }

    /// Get crypto price for a symbol
    pub fn get_crypto_price<'py>(
        &self,
        py: Python<'py>,
        symbol: &str,
    ) -> PyResult<Option<f64>> {
        let crypto_prices = self.crypto_prices.clone();
        let symbol = symbol.to_string();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                crypto_prices
                    .read()
                    .await
                    .get(&symbol)
                    .map(|p| p.price)
            })
        });

        Ok(result.map(|d| d.try_into().unwrap_or(0.0)))
    }

    /// Get best opportunity across all markets
    /// Returns the market with the lowest combined price (highest margin)
    pub fn get_best_opportunity<'py>(&self, py: Python<'py>) -> PyResult<Option<PyObject>> {
        let snapshots = self.snapshots.clone();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = snapshots.read().await;
                guard
                    .values()
                    .filter(|s| s.combined > Decimal::ZERO && s.combined < Decimal::ONE)
                    .min_by_key(|s| s.combined)
                    .cloned()
            })
        });

        match result {
            Some(snap) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("market_id", &snap.market_id)?;
                dict.set_item("coin", &snap.coin)?;
                dict.set_item("yes_ask", snap.yes_ask.to_string())?;
                dict.set_item("yes_ask_size", snap.yes_ask_size.to_string())?;
                dict.set_item("no_ask", snap.no_ask.to_string())?;
                dict.set_item("no_ask_size", snap.no_ask_size.to_string())?;
                dict.set_item("combined", snap.combined.to_string())?;
                dict.set_item("timestamp", snap.timestamp)?;
                Ok(Some(dict.into()))
            }
            None => Ok(None),
        }
    }

    /// Get count of cached markets
    pub fn market_count<'py>(&self, py: Python<'py>) -> PyResult<usize> {
        let snapshots = self.snapshots.clone();

        let count = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async { snapshots.read().await.len() })
        });

        Ok(count)
    }

    /// Clear all cached data
    pub fn clear<'py>(&self, py: Python<'py>) -> PyResult<()> {
        let snapshots = self.snapshots.clone();
        let crypto_prices = self.crypto_prices.clone();

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                snapshots.write().await.clear();
                crypto_prices.write().await.clear();
            });
        });

        Ok(())
    }
}

impl Default for PriceFeedCache {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal_macros::dec;

    #[test]
    fn test_market_snapshot_from_books() {
        let yes_asks = vec![(dec!(0.47), dec!(50.0))];
        let no_asks = vec![(dec!(0.48), dec!(30.0))];

        let snap = MarketSnapshot::from_books("BTC-15MIN", "BTC", &yes_asks, &no_asks);

        assert_eq!(snap.coin, "BTC");
        assert_eq!(snap.yes_ask, dec!(0.47));
        assert_eq!(snap.yes_ask_size, dec!(50.0));
        assert_eq!(snap.no_ask, dec!(0.48));
        assert_eq!(snap.no_ask_size, dec!(30.0));
        assert_eq!(snap.combined, dec!(0.95));
    }

    #[test]
    fn test_empty_books() {
        let snap = MarketSnapshot::from_books("BTC-15MIN", "BTC", &[], &[]);

        assert_eq!(snap.yes_ask, Decimal::ZERO);
        assert_eq!(snap.no_ask, Decimal::ZERO);
        assert_eq!(snap.combined, Decimal::ZERO);
    }
}

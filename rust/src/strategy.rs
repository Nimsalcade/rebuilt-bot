//! Gabagool Arbitrage Strategy Module
//!
//! Detects arbitrage opportunities in Polymarket 15-minute crypto markets.
//! Core logic: If YES_ask + NO_ask < $1.00 (minus costs), profit is guaranteed.
//!
//! Target: <1ms per check (vs ~30-50ms Python)

use pyo3::prelude::*;
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Configuration for the gabagool strategy
#[derive(Debug, Clone)]
pub struct StrategyConfig {
    /// Minimum margin (profit percentage) to consider trading
    pub min_margin: Decimal,
    /// Gas cost per transaction (2 txns per arb: YES + NO)
    pub gas_per_tx: Decimal,
    /// Maximum position size per trade
    pub max_position_size: Decimal,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            min_margin: dec!(0.005),    // 0.5% minimum margin
            gas_per_tx: dec!(0.003),    // $0.003 per tx (Polygon)
            max_position_size: dec!(100), // $100 max
        }
    }
}

/// Result of arbitrage detection
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArbitrageOpportunity {
    /// Market identifier
    pub market_id: String,
    /// Coin symbol (BTC, ETH, SOL, XRP)
    pub coin: String,
    /// YES ask price
    pub yes_ask: Decimal,
    /// NO ask price
    pub no_ask: Decimal,
    /// Combined price (yes_ask + no_ask)
    pub combined: Decimal,
    /// Gross margin (1.0 - combined)
    pub gross_margin: Decimal,
    /// Net margin after gas
    pub net_margin: Decimal,
    /// Recommended position size (limited by liquidity)
    pub position_size: Decimal,
    /// Expected profit
    pub expected_profit: Decimal,
    /// Liquidity at YES best ask
    pub yes_liquidity: Decimal,
    /// Liquidity at NO best ask
    pub no_liquidity: Decimal,
}

/// Thread-safe gabagool strategy
#[pyclass]
#[derive(Clone)]
pub struct GabagoolStrategy {
    config: StrategyConfig,
    /// Markets with active positions (to avoid doubling up)
    active_markets: Arc<RwLock<HashSet<String>>>,
}

#[pymethods]
impl GabagoolStrategy {
    /// Create new strategy with configuration
    #[new]
    #[pyo3(signature = (min_margin=0.005, gas_per_tx=0.003, max_position_size=100.0))]
    pub fn new(min_margin: f64, gas_per_tx: f64, max_position_size: f64) -> Self {
        Self {
            config: StrategyConfig {
                min_margin: Decimal::try_from(min_margin).unwrap_or(dec!(0.005)),
                gas_per_tx: Decimal::try_from(gas_per_tx).unwrap_or(dec!(0.003)),
                max_position_size: Decimal::try_from(max_position_size).unwrap_or(dec!(100)),
            },
            active_markets: Arc::new(RwLock::new(HashSet::new())),
        }
    }

    /// Detect arbitrage opportunity
    ///
    /// Returns profit amount if opportunity exists, None otherwise.
    /// This is the HOT PATH - must be <1ms.
    #[pyo3(signature = (market_id, coin, yes_ask, no_ask, yes_liquidity, no_liquidity))]
    pub fn detect_arbitrage<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        coin: &str,
        yes_ask: f64,
        no_ask: f64,
        yes_liquidity: f64,
        no_liquidity: f64,
    ) -> PyResult<Option<PyObject>> {
        // Convert to Decimal for precision
        let yes_ask = Decimal::try_from(yes_ask).unwrap_or(Decimal::ZERO);
        let no_ask = Decimal::try_from(no_ask).unwrap_or(Decimal::ZERO);
        let yes_liq = Decimal::try_from(yes_liquidity).unwrap_or(Decimal::ZERO);
        let no_liq = Decimal::try_from(no_liquidity).unwrap_or(Decimal::ZERO);

        // Quick rejection: invalid prices
        if yes_ask <= Decimal::ZERO || no_ask <= Decimal::ZERO {
            return Ok(None);
        }

        // Calculate combined and margin
        let combined = yes_ask + no_ask;
        let gross_margin = Decimal::ONE - combined;

        // Quick rejection: no gross margin
        if gross_margin <= Decimal::ZERO {
            return Ok(None);
        }

        // Calculate gas cost (2 transactions)
        let total_gas = self.config.gas_per_tx * dec!(2);

        // Calculate position size (limited by liquidity at best price)
        let min_side_liquidity = yes_liq.min(no_liq);
        let liquidity_limited_size = min_side_liquidity * dec!(2); // Can buy both sides
        let position_size = self
            .config
            .max_position_size
            .min(liquidity_limited_size)
            .max(Decimal::ZERO);

        // No liquidity = no trade
        if position_size <= Decimal::ZERO {
            return Ok(None);
        }

        // Calculate expected profit
        let gross_profit = position_size * gross_margin;
        let expected_profit = gross_profit - total_gas;

        // Calculate net margin percentage
        let net_margin = if position_size > Decimal::ZERO {
            expected_profit / position_size
        } else {
            Decimal::ZERO
        };

        // Check minimum margin threshold
        if net_margin < self.config.min_margin {
            return Ok(None);
        }

        // Check if we already have a position in this market
        let active_markets = self.active_markets.clone();
        let market_id_owned = market_id.to_string();

        let already_active = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = active_markets.read().await;
                guard.contains(&market_id_owned)
            })
        });

        if already_active {
            return Ok(None);
        }

        // Build opportunity result
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("market_id", market_id)?;
        dict.set_item("coin", coin)?;
        dict.set_item("yes_ask", yes_ask.to_string())?;
        dict.set_item("no_ask", no_ask.to_string())?;
        dict.set_item("combined", combined.to_string())?;
        dict.set_item("gross_margin", gross_margin.to_string())?;
        dict.set_item("net_margin", net_margin.to_string())?;
        dict.set_item("position_size", position_size.to_string())?;
        dict.set_item("expected_profit", expected_profit.to_string())?;
        dict.set_item("yes_liquidity", yes_liq.to_string())?;
        dict.set_item("no_liquidity", no_liq.to_string())?;

        Ok(Some(dict.into()))
    }

    /// Mark a market as having an active position
    pub fn mark_active<'py>(&self, py: Python<'py>, market_id: &str) -> PyResult<()> {
        let active_markets = self.active_markets.clone();
        let market_id = market_id.to_string();

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                active_markets.write().await.insert(market_id);
            });
        });

        Ok(())
    }

    /// Mark a market as no longer having an active position
    pub fn mark_inactive<'py>(&self, py: Python<'py>, market_id: &str) -> PyResult<()> {
        let active_markets = self.active_markets.clone();
        let market_id = market_id.to_string();

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                active_markets.write().await.remove(&market_id);
            });
        });

        Ok(())
    }

    /// Get count of active markets
    pub fn active_market_count<'py>(&self, py: Python<'py>) -> PyResult<usize> {
        let active_markets = self.active_markets.clone();

        let count = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async { active_markets.read().await.len() })
        });

        Ok(count)
    }

    /// Clear all active markets (for testing/reset)
    pub fn clear_active<'py>(&self, py: Python<'py>) -> PyResult<()> {
        let active_markets = self.active_markets.clone();

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                active_markets.write().await.clear();
            });
        });

        Ok(())
    }

    /// Get current configuration as dict
    pub fn get_config<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("min_margin", self.config.min_margin.to_string())?;
        dict.set_item("gas_per_tx", self.config.gas_per_tx.to_string())?;
        dict.set_item("max_position_size", self.config.max_position_size.to_string())?;
        Ok(dict.into())
    }
}

impl Default for GabagoolStrategy {
    fn default() -> Self {
        Self::new(0.005, 0.003, 100.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strategy_config_default() {
        let config = StrategyConfig::default();
        assert_eq!(config.min_margin, dec!(0.005));
        assert_eq!(config.gas_per_tx, dec!(0.003));
        assert_eq!(config.max_position_size, dec!(100));
    }

    #[test]
    fn test_arbitrage_calculation() {
        // Manual calculation test (without Python)
        let yes_ask = dec!(0.47);
        let no_ask = dec!(0.48);
        let combined = yes_ask + no_ask; // 0.95
        let gross_margin = Decimal::ONE - combined; // 0.05 (5%)

        assert_eq!(combined, dec!(0.95));
        assert_eq!(gross_margin, dec!(0.05));

        // With $100 position and $0.006 gas
        let position = dec!(100);
        let gas = dec!(0.006);
        let gross_profit = position * gross_margin; // $5.00
        let net_profit = gross_profit - gas; // $4.994

        assert_eq!(gross_profit, dec!(5));
        assert_eq!(net_profit, dec!(4.994));
    }

    #[test]
    fn test_no_opportunity_when_combined_above_one() {
        // When combined >= 1.0, no arbitrage
        let yes_ask = dec!(0.52);
        let no_ask = dec!(0.52);
        let combined = yes_ask + no_ask; // 1.04
        let gross_margin = Decimal::ONE - combined; // -0.04

        assert!(gross_margin < Decimal::ZERO);
    }
}

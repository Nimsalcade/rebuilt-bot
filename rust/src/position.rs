//! Position Tracking Module
//!
//! Thread-safe position tracking for gabagool arbitrage.
//! Tracks YES/NO positions per market with cost basis and P&L.
//!
//! Target: <1ms per operation (vs ~50ms Python with locks)

use pyo3::prelude::*;
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

/// A single position leg (YES or NO side)
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PositionLeg {
    /// Number of shares held
    pub shares: Decimal,
    /// Total cost paid (in dollars)
    pub cost: Decimal,
    /// Average price per share
    pub avg_price: Decimal,
}

impl PositionLeg {
    /// Add shares at a given price
    pub fn add(&mut self, shares: Decimal, price: Decimal) {
        let new_cost = shares * price;
        self.cost += new_cost;
        self.shares += shares;
        if self.shares > Decimal::ZERO {
            self.avg_price = self.cost / self.shares;
        }
    }

    /// Check if position is empty
    pub fn is_empty(&self) -> bool {
        self.shares == Decimal::ZERO
    }
}

/// A complete arbitrage position (YES + NO on same market)
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ArbPosition {
    /// Market identifier (e.g., "BTC-15MIN-2026-01-27T18:30")
    pub market_id: String,
    /// YES side position
    pub yes: PositionLeg,
    /// NO side position
    pub no: PositionLeg,
    /// Total gas fees paid
    pub gas_paid: Decimal,
    /// Timestamp when position was opened
    pub opened_at: String,
    /// Whether position has been settled
    pub settled: bool,
    /// Realized P&L (set after settlement)
    pub realized_pnl: Option<Decimal>,
}

impl ArbPosition {
    /// Create new position for a market
    pub fn new(market_id: &str) -> Self {
        Self {
            market_id: market_id.to_string(),
            opened_at: chrono::Utc::now().to_rfc3339(),
            ..Default::default()
        }
    }

    /// Total cost basis (YES cost + NO cost + gas)
    pub fn total_cost(&self) -> Decimal {
        self.yes.cost + self.no.cost + self.gas_paid
    }

    /// Check if we have a complete pair (both YES and NO)
    pub fn has_complete_pair(&self) -> bool {
        !self.yes.is_empty() && !self.no.is_empty()
    }

    /// Minimum matched shares (the arbitrage profit is on matched shares)
    pub fn matched_shares(&self) -> Decimal {
        self.yes.shares.min(self.no.shares)
    }

    /// Guaranteed profit from matched shares
    /// In gabagool arb: YES + NO = $1.00 payout regardless of outcome
    pub fn guaranteed_profit(&self) -> Decimal {
        let matched = self.matched_shares();
        // Payout is $1.00 per matched share pair
        matched - self.total_cost()
    }

    /// Settle position with outcome
    pub fn settle(&mut self, yes_won: bool) -> Decimal {
        let payout = if yes_won {
            self.yes.shares // YES pays $1.00 per share
        } else {
            self.no.shares // NO pays $1.00 per share
        };

        let pnl = payout - self.total_cost();
        self.realized_pnl = Some(pnl);
        self.settled = true;
        pnl
    }
}

/// Thread-safe position tracker
#[pyclass]
#[derive(Clone)]
pub struct PositionTracker {
    positions: Arc<RwLock<HashMap<String, ArbPosition>>>,
    /// Total realized P&L
    total_realized_pnl: Arc<RwLock<Decimal>>,
}

#[pymethods]
impl PositionTracker {
    /// Create a new position tracker
    #[new]
    pub fn new() -> Self {
        Self {
            positions: Arc::new(RwLock::new(HashMap::new())),
            total_realized_pnl: Arc::new(RwLock::new(Decimal::ZERO)),
        }
    }

    /// Add YES position for a market
    pub fn add_yes_position<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        shares: f64,
        cost: f64,
    ) -> PyResult<()> {
        let positions = self.positions.clone();
        let market_id = market_id.to_string();
        let shares = Decimal::try_from(shares).unwrap_or(Decimal::ZERO);
        let price = if shares > Decimal::ZERO {
            Decimal::try_from(cost).unwrap_or(Decimal::ZERO) / shares
        } else {
            Decimal::ZERO
        };

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let mut guard = positions.write().await;
                let position = guard
                    .entry(market_id.clone())
                    .or_insert_with(|| ArbPosition::new(&market_id));
                position.yes.add(shares, price);
            });
        });

        Ok(())
    }

    /// Add NO position for a market
    pub fn add_no_position<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        shares: f64,
        cost: f64,
    ) -> PyResult<()> {
        let positions = self.positions.clone();
        let market_id = market_id.to_string();
        let shares = Decimal::try_from(shares).unwrap_or(Decimal::ZERO);
        let price = if shares > Decimal::ZERO {
            Decimal::try_from(cost).unwrap_or(Decimal::ZERO) / shares
        } else {
            Decimal::ZERO
        };

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let mut guard = positions.write().await;
                let position = guard
                    .entry(market_id.clone())
                    .or_insert_with(|| ArbPosition::new(&market_id));
                position.no.add(shares, price);
            });
        });

        Ok(())
    }

    /// Add gas cost to a position
    pub fn add_gas<'py>(&self, py: Python<'py>, market_id: &str, gas: f64) -> PyResult<()> {
        let positions = self.positions.clone();
        let market_id = market_id.to_string();
        let gas = Decimal::try_from(gas).unwrap_or(Decimal::ZERO);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let mut guard = positions.write().await;
                if let Some(position) = guard.get_mut(&market_id) {
                    position.gas_paid += gas;
                }
            });
        });

        Ok(())
    }

    /// Check if market has a complete pair (YES + NO)
    pub fn has_complete_pair<'py>(&self, py: Python<'py>, market_id: &str) -> PyResult<bool> {
        let positions = self.positions.clone();
        let market_id = market_id.to_string();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = positions.read().await;
                guard
                    .get(&market_id)
                    .map(|p| p.has_complete_pair())
                    .unwrap_or(false)
            })
        });

        Ok(result)
    }

    /// Get position details as a dict
    pub fn get_position<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let positions = self.positions.clone();
        let market_id = market_id.to_string();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = positions.read().await;
                guard.get(&market_id).cloned()
            })
        });

        match result {
            Some(pos) => {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("market_id", &pos.market_id)?;
                dict.set_item("yes_shares", pos.yes.shares.to_string())?;
                dict.set_item("yes_cost", pos.yes.cost.to_string())?;
                dict.set_item("no_shares", pos.no.shares.to_string())?;
                dict.set_item("no_cost", pos.no.cost.to_string())?;
                dict.set_item("gas_paid", pos.gas_paid.to_string())?;
                dict.set_item("total_cost", pos.total_cost().to_string())?;
                dict.set_item("matched_shares", pos.matched_shares().to_string())?;
                dict.set_item("guaranteed_profit", pos.guaranteed_profit().to_string())?;
                dict.set_item("has_complete_pair", pos.has_complete_pair())?;
                dict.set_item("settled", pos.settled)?;
                Ok(Some(dict.into()))
            }
            None => Ok(None),
        }
    }

    /// Get all positions as a list of dicts
    pub fn get_all_positions<'py>(&self, py: Python<'py>) -> PyResult<PyObject> {
        let positions = self.positions.clone();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = positions.read().await;
                guard.values().cloned().collect::<Vec<_>>()
            })
        });

        let list = pyo3::types::PyList::empty(py);
        for pos in result {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("market_id", &pos.market_id)?;
            dict.set_item("yes_shares", pos.yes.shares.to_string())?;
            dict.set_item("yes_cost", pos.yes.cost.to_string())?;
            dict.set_item("no_shares", pos.no.shares.to_string())?;
            dict.set_item("no_cost", pos.no.cost.to_string())?;
            dict.set_item("gas_paid", pos.gas_paid.to_string())?;
            dict.set_item("total_cost", pos.total_cost().to_string())?;
            dict.set_item("matched_shares", pos.matched_shares().to_string())?;
            dict.set_item("guaranteed_profit", pos.guaranteed_profit().to_string())?;
            dict.set_item("has_complete_pair", pos.has_complete_pair())?;
            dict.set_item("settled", pos.settled)?;
            list.append(dict)?;
        }

        Ok(list.into())
    }

    /// Get total realized P&L
    pub fn get_total_pnl<'py>(&self, py: Python<'py>) -> PyResult<f64> {
        let pnl = self.total_realized_pnl.clone();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = pnl.read().await;
                *guard
            })
        });

        Ok(result.try_into().unwrap_or(0.0))
    }

    /// Get count of open positions
    pub fn open_position_count<'py>(&self, py: Python<'py>) -> PyResult<usize> {
        let positions = self.positions.clone();

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                let guard = positions.read().await;
                guard.values().filter(|p| !p.settled).count()
            })
        });

        Ok(result)
    }

    /// Clear all positions (for testing/reset)
    pub fn clear<'py>(&self, py: Python<'py>) -> PyResult<()> {
        let positions = self.positions.clone();
        let pnl = self.total_realized_pnl.clone();

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async {
                positions.write().await.clear();
                *pnl.write().await = Decimal::ZERO;
            });
        });

        Ok(())
    }
}

impl Default for PositionTracker {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_position_leg_add() {
        let mut leg = PositionLeg::default();
        leg.add(dec!(100), dec!(0.45)); // 100 shares at $0.45

        assert_eq!(leg.shares, dec!(100));
        assert_eq!(leg.cost, dec!(45)); // 100 * 0.45 = 45
        assert_eq!(leg.avg_price, dec!(0.45));
    }

    #[test]
    fn test_arb_position_profit() {
        let mut pos = ArbPosition::new("BTC-15MIN");

        // Buy 100 YES at $0.47
        pos.yes.add(dec!(100), dec!(0.47));
        // Buy 100 NO at $0.48
        pos.no.add(dec!(100), dec!(0.48));
        // Gas: $0.006
        pos.gas_paid = dec!(0.006);

        // Total cost: 47 + 48 + 0.006 = 95.006
        assert_eq!(pos.total_cost(), dec!(95.006));

        // Matched: 100 shares
        assert_eq!(pos.matched_shares(), dec!(100));

        // Guaranteed profit: 100 - 95.006 = 4.994
        assert_eq!(pos.guaranteed_profit(), dec!(4.994));

        assert!(pos.has_complete_pair());
    }

    #[test]
    fn test_settlement() {
        let mut pos = ArbPosition::new("BTC-15MIN");
        pos.yes.add(dec!(100), dec!(0.47));
        pos.no.add(dec!(100), dec!(0.48));

        // YES wins - payout is 100 (yes shares)
        // Cost was 47 + 48 = 95
        // PnL = 100 - 95 = 5
        let pnl = pos.settle(true);
        assert_eq!(pnl, dec!(5));
        assert!(pos.settled);
    }
}

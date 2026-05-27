//! Order Execution Module
//!
//! Handles order execution for gabagool arbitrage.
//! Key feature: Parallel execution of YES and NO orders using tokio::join!
//!
//! Target: 20-30ms for both orders (vs 150ms+ sequential in Python)
//!
//! Note: This module supports both paper trading (simulation) and live execution.
//! Live execution requires the Polymarket SDK integration.

use pyo3::prelude::*;
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// Order side
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Yes,
    No,
}

impl std::fmt::Display for Side {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Side::Yes => write!(f, "YES"),
            Side::No => write!(f, "NO"),
        }
    }
}

/// Order status
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum OrderStatus {
    Pending,
    Filled,
    PartiallyFilled,
    Rejected(String),
    Cancelled,
}

/// Result of a single order execution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderResult {
    /// Order ID (from exchange or generated for paper)
    pub order_id: String,
    /// Side (YES or NO)
    pub side: Side,
    /// Requested size
    pub requested_size: Decimal,
    /// Filled size
    pub filled_size: Decimal,
    /// Fill price
    pub fill_price: Decimal,
    /// Total cost (filled_size * fill_price)
    pub total_cost: Decimal,
    /// Gas cost
    pub gas_cost: Decimal,
    /// Order status
    pub status: OrderStatus,
    /// Execution time in milliseconds
    pub execution_time_ms: u64,
    /// Error message if failed
    pub error: Option<String>,
}

impl OrderResult {
    /// Create a successful fill result
    pub fn filled(side: Side, size: Decimal, price: Decimal, gas: Decimal, time_ms: u64) -> Self {
        Self {
            order_id: uuid::Uuid::new_v4().to_string(),
            side,
            requested_size: size,
            filled_size: size,
            fill_price: price,
            total_cost: size * price,
            gas_cost: gas,
            status: OrderStatus::Filled,
            execution_time_ms: time_ms,
            error: None,
        }
    }

    /// Create a rejected result
    pub fn rejected(side: Side, size: Decimal, reason: &str) -> Self {
        Self {
            order_id: String::new(),
            side,
            requested_size: size,
            filled_size: Decimal::ZERO,
            fill_price: Decimal::ZERO,
            total_cost: Decimal::ZERO,
            gas_cost: Decimal::ZERO,
            status: OrderStatus::Rejected(reason.to_string()),
            execution_time_ms: 0,
            error: Some(reason.to_string()),
        }
    }
}

/// Result of an arbitrage execution (YES + NO)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArbitrageResult {
    /// Market ID
    pub market_id: String,
    /// YES order result
    pub yes_result: OrderResult,
    /// NO order result
    pub no_result: OrderResult,
    /// Total execution time (both orders, parallel)
    pub total_time_ms: u64,
    /// Whether both orders were successful
    pub success: bool,
    /// Total cost (YES + NO + gas)
    pub total_cost: Decimal,
    /// Expected profit (based on fills)
    pub expected_profit: Decimal,
}

/// Order executor for arbitrage trades
///
/// Supports paper trading mode for testing without real orders.
#[pyclass]
#[derive(Clone)]
pub struct OrderExecutor {
    /// Paper trading mode (no real orders)
    paper_mode: Arc<AtomicBool>,
    /// Gas cost per transaction
    gas_per_tx: Decimal,
    /// Simulated latency for paper trades (ms)
    paper_latency_ms: u64,
}

#[pymethods]
impl OrderExecutor {
    /// Create new order executor
    ///
    /// Args:
    ///     paper_mode: If true, simulate orders without real execution
    ///     gas_per_tx: Gas cost per transaction in dollars
    #[new]
    #[pyo3(signature = (paper_mode=true, gas_per_tx=0.003))]
    pub fn new(paper_mode: bool, gas_per_tx: f64) -> Self {
        Self {
            paper_mode: Arc::new(AtomicBool::new(paper_mode)),
            gas_per_tx: Decimal::try_from(gas_per_tx).unwrap_or(dec!(0.003)),
            paper_latency_ms: 5, // Simulate 5ms latency in paper mode
        }
    }

    /// Check if in paper trading mode
    pub fn is_paper_mode(&self) -> bool {
        self.paper_mode.load(Ordering::SeqCst)
    }

    /// Set paper trading mode
    pub fn set_paper_mode(&self, enabled: bool) {
        self.paper_mode.store(enabled, Ordering::SeqCst);
    }

    /// Execute arbitrage (YES + NO orders in parallel)
    ///
    /// This is the HOT PATH for execution.
    /// In paper mode: Simulates instant fills at specified prices.
    /// In live mode: Would use Polymarket SDK (not yet implemented).
    #[pyo3(signature = (market_id, yes_price, no_price, size))]
    pub fn execute_arbitrage<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        yes_price: f64,
        no_price: f64,
        size: f64,
    ) -> PyResult<PyObject> {
        let market_id = market_id.to_string();
        let yes_price = Decimal::try_from(yes_price).unwrap_or(Decimal::ZERO);
        let no_price = Decimal::try_from(no_price).unwrap_or(Decimal::ZERO);
        let size = Decimal::try_from(size).unwrap_or(Decimal::ZERO);
        let gas = self.gas_per_tx;
        let paper_mode = self.paper_mode.load(Ordering::SeqCst);
        let latency = self.paper_latency_ms;

        // Execute in background thread to not block Python
        let result = py.allow_threads(move || {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async move {
                let start = std::time::Instant::now();

                if paper_mode {
                    // Paper trading: simulate parallel execution
                    let (yes_result, no_result) = tokio::join!(
                        simulate_order(Side::Yes, size, yes_price, gas, latency),
                        simulate_order(Side::No, size, no_price, gas, latency),
                    );

                    let total_time = start.elapsed().as_millis() as u64;
                    let success = matches!(yes_result.status, OrderStatus::Filled)
                        && matches!(no_result.status, OrderStatus::Filled);

                    let total_cost =
                        yes_result.total_cost + no_result.total_cost + gas + gas;

                    // Expected profit: $1.00 per share - total cost
                    let expected_profit = yes_result.filled_size - total_cost;

                    ArbitrageResult {
                        market_id,
                        yes_result,
                        no_result,
                        total_time_ms: total_time,
                        success,
                        total_cost,
                        expected_profit,
                    }
                } else {
                    // Live mode: Not yet implemented
                    // Would use polymarket_client_sdk here
                    let yes_result =
                        OrderResult::rejected(Side::Yes, size, "Live mode not implemented");
                    let no_result =
                        OrderResult::rejected(Side::No, size, "Live mode not implemented");

                    ArbitrageResult {
                        market_id,
                        yes_result,
                        no_result,
                        total_time_ms: 0,
                        success: false,
                        total_cost: Decimal::ZERO,
                        expected_profit: Decimal::ZERO,
                    }
                }
            })
        });

        // Convert to Python dict
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("market_id", &result.market_id)?;
        dict.set_item("success", result.success)?;
        dict.set_item("total_time_ms", result.total_time_ms)?;
        dict.set_item("total_cost", result.total_cost.to_string())?;
        dict.set_item("expected_profit", result.expected_profit.to_string())?;

        // YES result
        let yes_dict = pyo3::types::PyDict::new(py);
        yes_dict.set_item("order_id", &result.yes_result.order_id)?;
        yes_dict.set_item("side", "YES")?;
        yes_dict.set_item("filled_size", result.yes_result.filled_size.to_string())?;
        yes_dict.set_item("fill_price", result.yes_result.fill_price.to_string())?;
        yes_dict.set_item("total_cost", result.yes_result.total_cost.to_string())?;
        yes_dict.set_item("gas_cost", result.yes_result.gas_cost.to_string())?;
        yes_dict.set_item("execution_time_ms", result.yes_result.execution_time_ms)?;
        yes_dict.set_item(
            "status",
            match &result.yes_result.status {
                OrderStatus::Filled => "filled",
                OrderStatus::PartiallyFilled => "partial",
                OrderStatus::Rejected(_) => "rejected",
                OrderStatus::Pending => "pending",
                OrderStatus::Cancelled => "cancelled",
            },
        )?;
        dict.set_item("yes_result", yes_dict)?;

        // NO result
        let no_dict = pyo3::types::PyDict::new(py);
        no_dict.set_item("order_id", &result.no_result.order_id)?;
        no_dict.set_item("side", "NO")?;
        no_dict.set_item("filled_size", result.no_result.filled_size.to_string())?;
        no_dict.set_item("fill_price", result.no_result.fill_price.to_string())?;
        no_dict.set_item("total_cost", result.no_result.total_cost.to_string())?;
        no_dict.set_item("gas_cost", result.no_result.gas_cost.to_string())?;
        no_dict.set_item("execution_time_ms", result.no_result.execution_time_ms)?;
        no_dict.set_item(
            "status",
            match &result.no_result.status {
                OrderStatus::Filled => "filled",
                OrderStatus::PartiallyFilled => "partial",
                OrderStatus::Rejected(_) => "rejected",
                OrderStatus::Pending => "pending",
                OrderStatus::Cancelled => "cancelled",
            },
        )?;
        dict.set_item("no_result", no_dict)?;

        Ok(dict.into())
    }

    /// Execute a single order (for testing)
    #[pyo3(signature = (market_id, side, price, size))]
    pub fn execute_single<'py>(
        &self,
        py: Python<'py>,
        market_id: &str,
        side: &str,
        price: f64,
        size: f64,
    ) -> PyResult<PyObject> {
        let side = match side.to_uppercase().as_str() {
            "YES" => Side::Yes,
            "NO" => Side::No,
            _ => return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>("Side must be YES or NO")),
        };

        let price = Decimal::try_from(price).unwrap_or(Decimal::ZERO);
        let size = Decimal::try_from(size).unwrap_or(Decimal::ZERO);
        let gas = self.gas_per_tx;
        let paper_mode = self.paper_mode.load(Ordering::SeqCst);
        let latency = self.paper_latency_ms;

        let result = py.allow_threads(move || {
            let rt = tokio::runtime::Runtime::new().unwrap();
            rt.block_on(async move {
                if paper_mode {
                    simulate_order(side, size, price, gas, latency).await
                } else {
                    OrderResult::rejected(side, size, "Live mode not implemented")
                }
            })
        });

        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("order_id", &result.order_id)?;
        dict.set_item("side", result.side.to_string())?;
        dict.set_item("filled_size", result.filled_size.to_string())?;
        dict.set_item("fill_price", result.fill_price.to_string())?;
        dict.set_item("total_cost", result.total_cost.to_string())?;
        dict.set_item("gas_cost", result.gas_cost.to_string())?;
        dict.set_item("execution_time_ms", result.execution_time_ms)?;
        dict.set_item(
            "status",
            match &result.status {
                OrderStatus::Filled => "filled",
                OrderStatus::PartiallyFilled => "partial",
                OrderStatus::Rejected(r) => r.as_str(),
                OrderStatus::Pending => "pending",
                OrderStatus::Cancelled => "cancelled",
            },
        )?;

        Ok(dict.into())
    }

    /// Get gas cost per transaction
    pub fn get_gas_per_tx(&self) -> f64 {
        self.gas_per_tx.try_into().unwrap_or(0.003)
    }
}

/// Simulate order execution (for paper trading)
async fn simulate_order(
    side: Side,
    size: Decimal,
    price: Decimal,
    gas: Decimal,
    latency_ms: u64,
) -> OrderResult {
    // Simulate network latency
    tokio::time::sleep(tokio::time::Duration::from_millis(latency_ms)).await;

    let start = std::time::Instant::now();

    // In paper mode, assume perfect fills
    let result = OrderResult::filled(side, size, price, gas, start.elapsed().as_millis() as u64);

    result
}

impl Default for OrderExecutor {
    fn default() -> Self {
        Self::new(true, 0.003)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_order_result_filled() {
        let result = OrderResult::filled(Side::Yes, dec!(100), dec!(0.47), dec!(0.003), 5);

        assert_eq!(result.side, Side::Yes);
        assert_eq!(result.filled_size, dec!(100));
        assert_eq!(result.fill_price, dec!(0.47));
        assert_eq!(result.total_cost, dec!(47)); // 100 * 0.47
        assert!(matches!(result.status, OrderStatus::Filled));
    }

    #[test]
    fn test_order_result_rejected() {
        let result = OrderResult::rejected(Side::No, dec!(100), "Insufficient balance");

        assert_eq!(result.side, Side::No);
        assert_eq!(result.filled_size, Decimal::ZERO);
        assert!(matches!(result.status, OrderStatus::Rejected(_)));
        assert!(result.error.is_some());
    }

    #[tokio::test]
    async fn test_simulate_order() {
        let result = simulate_order(Side::Yes, dec!(100), dec!(0.47), dec!(0.003), 1).await;

        assert_eq!(result.side, Side::Yes);
        assert_eq!(result.filled_size, dec!(100));
        assert!(matches!(result.status, OrderStatus::Filled));
    }
}

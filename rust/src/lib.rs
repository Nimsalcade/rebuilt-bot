//! Gabagool Rust Hot Path
//!
//! High-performance Rust implementation of the gabagool arbitrage bot's
//! time-critical components. Exposed to Python via PyO3.
//!
//! # Architecture
//!
//! ```text
//! Python Layer (Orchestration)
//!        │
//!        │ PyO3 FFI
//!        ▼
//! ┌─────────────────────────────────────┐
//! │         Rust Hot Path               │
//! │                                     │
//! │  ws_handler  - Price cache (5-10ms) │
//! │  strategy    - Detection (<1ms)     │
//! │  execution   - Orders (20-30ms)     │
//! │  position    - Tracking (<1ms)      │
//! └─────────────────────────────────────┘
//! ```
//!
//! # Target Performance
//!
//! - Total hot path: <40ms (vs 380ms Python)
//! - Speedup: 9-10x
//!
//! # Usage from Python
//!
//! ```python
//! import gabagool_rust
//!
//! # Check module is loaded
//! print(gabagool_rust.health_check())
//!
//! # Create components
//! cache = gabagool_rust.PriceFeedCache()
//! strategy = gabagool_rust.GabagoolStrategy(min_margin=0.005)
//! executor = gabagool_rust.OrderExecutor(paper_mode=True)
//! tracker = gabagool_rust.PositionTracker()
//!
//! # Update price cache (from Python WebSocket)
//! cache.update_snapshot("BTC-15MIN", "BTC", 0.47, 50.0, 0.48, 30.0)
//!
//! # Detect arbitrage
//! opp = strategy.detect_arbitrage("BTC-15MIN", "BTC", 0.47, 0.48, 50.0, 30.0)
//! if opp:
//!     # Execute (paper mode)
//!     result = executor.execute_arbitrage("BTC-15MIN", 0.47, 0.48, 30.0)
//!     if result['success']:
//!         tracker.add_yes_position("BTC-15MIN", 30.0, 14.1)
//!         tracker.add_no_position("BTC-15MIN", 30.0, 14.4)
//! ```

use pyo3::prelude::*;

// Module declarations
mod data_collector;
mod execution;
mod position;
mod storage;
mod strategy;
mod ws_handler;

// Re-export types for internal use
pub use data_collector::DataCollector;
pub use execution::OrderExecutor;
pub use position::PositionTracker;
pub use storage::DataStorage;
pub use strategy::GabagoolStrategy;
pub use ws_handler::PriceFeedCache;

/// Gabagool Rust Hot Path Module
///
/// Provides high-performance implementations of:
/// - PriceFeedCache: Thread-safe price/order book caching
/// - GabagoolStrategy: Arbitrage opportunity detection
/// - OrderExecutor: Parallel order execution (paper + live)
/// - PositionTracker: Thread-safe position tracking
#[pymodule]
fn gabagool_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Module metadata
    m.add("__version__", "0.1.0")?;
    m.add("__doc__", "Rust hot path for gabagool Polymarket arbitrage")?;

    // Health check function
    m.add_function(wrap_pyfunction!(health_check, m)?)?;

    // Register classes - Hot Path Components
    m.add_class::<ws_handler::PriceFeedCache>()?;
    m.add_class::<strategy::GabagoolStrategy>()?;
    m.add_class::<execution::OrderExecutor>()?;
    m.add_class::<position::PositionTracker>()?;

    // Register classes - Data Collection Pipeline
    m.add_class::<storage::DataStorage>()?;
    m.add_class::<data_collector::DataCollector>()?;

    Ok(())
}

/// Health check to verify module is loaded and working
#[pyfunction]
fn health_check() -> PyResult<String> {
    Ok("gabagool_rust v0.1.0 - OK (6 components: PriceFeedCache, GabagoolStrategy, OrderExecutor, PositionTracker, DataStorage, DataCollector)".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_check() {
        let result = health_check().unwrap();
        assert!(result.contains("OK"));
        assert!(result.contains("4 components"));
    }
}

# Polymarket Historical Data Sources Assessment

*Research Date: January 26, 2026*

---

## Executive Summary

Historical order book data for Polymarket 15-minute crypto markets is available through multiple sources, ranging from free official APIs to premium third-party services. **The best option for your gabagool backtesting needs is a combination approach**: use the official CLOB API for price history, Goldsky subgraph for trade data, and consider PolymarketData.co for full order book snapshots if deeper analysis is needed.

---

## Source 1: Official Polymarket CLOB API (Free)

**URL**: https://clob.polymarket.com/prices-history

**Type**: Official API

**Data Available**:
- [x] Price history (timeseries)
- [x] Trade history (authenticated)
- [ ] Order book snapshots (current only, no historical)
- [x] 15-minute market coverage
- [x] Historical depth: Configurable via `interval` or `startTs/endTs`

**Access**:
- Method: REST API
- Cost: Free
- Rate limits: ~1,000 calls/hour for non-trading queries
- Authentication: None for price history, L2 header for trades

**Data Format**: JSON
```json
{
  "history": [
    {"t": 1697875200, "p": 0.48},
    {"t": 1697875260, "p": 0.49}
  ]
}
```

**Granularity**: Configurable (1m, 5m, 1h, 1d, etc.)

**Quality**: High - Official source

**Limitations**:
- Price history only (not full order book depth)
- No bid/ask spread history
- Trade history requires authentication

**Relevance Score**: 6/10 for gabagool

**Notes**: The CLOB provides detailed price history for each traded token. Fetches historical price data for a specified market token. Good for basic price trends but lacks order book depth needed for arbitrage analysis.

---

## Source 2: Polymarket Subgraph via Goldsky (Free)

**URL**: https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/

**Type**: Official (hosted by third-party)

**Data Available**:
- [ ] Order book snapshots
- [x] Trade history (OrderFilled events)
- [x] Market metadata
- [x] 15-minute market coverage
- [x] Historical depth: Full blockchain history

**Access**:
- Method: GraphQL API
- Cost: Free
- Rate limits: Generous

**Subgraphs Available**:
- Orders subgraph: `orderbook-subgraph/prod/gn`
- PNL subgraph: `pnl-subgraph/0.0.14/gn`

**Data Format**: GraphQL/JSON

**Quality**: High - On-chain data

**Relevance Score**: 7/10 for gabagool

**Notes**: Polymarket has written and open sourced a subgraph that provides, via a GraphQL query interface, useful aggregate calculations and event indexing for things like volume, user position, market and liquidity data.

---

## Source 3: warproxxx/poly_data (Free - Open Source)

**URL**: https://github.com/warproxxx/poly_data

**Type**: Community/Open Source

**Data Available**:
- [ ] Order book snapshots
- [x] Trade history (processed from Goldsky)
- [x] Market metadata
- [x] 15-minute market coverage (if markets existed)
- [x] Historical depth: Full history with downloadable snapshot

**Access**:
- Method: Download CSV / Run scripts
- Cost: Free
- Rate limits: N/A (local data)

**Data Format**: CSV
```
Fields: timestamp, market_id, maker, taker, price, usd_amount, token_amount, transactionHash
```

**Quality**: High - Well-maintained

**Relevance Score**: 8/10 for gabagool

**Notes**: First-time users: Download the latest data snapshot and extract it in the main repository directory before your first run. This will save you over 2 days of initial data collection time. This is from the same warproxxx whose poly-maker bot you're extracting components from. Excellent synergy.

---

## Source 4: PolymarketData.co (Paid - Premium)

**URL**: https://www.polymarketdata.co/

**Type**: Third-party premium service

**Data Available**:
- [x] **Full order book snapshots** (bids/asks depth)
- [x] Trade history
- [x] Market metadata
- [x] 15-minute market coverage
- [x] Historical depth: Full history

**Access**:
- Method: S3 dumps, direct database access, or custom exports
- Cost: Paid (pricing not public - contact required)
- Formats: SQL, Parquet, CSV, JSON

**Data Specs**:
The complete historical dataset for Polymarket. Full order book snapshots, volume and liquidity at 1-minute resolution. More than 5 Billion rows indexed in our database and growing every day.

**Sample Data Structure**:
```json
{
  "timestamp": "2026-01-15 02:02:00",
  "token_id": "847350984861295801...",
  "bids": [
    {"price": "0.49", "size": "1120"},
    {"price": "0.48", "size": "1066"}
  ],
  "asks": [
    {"price": "0.51", "size": "1120"},
    {"price": "0.52", "size": "1066"}
  ],
  "volume": 12450800.45,
  "liquidity": 1613500.75,
  "spread": 0.02
}
```

**Quality**: Professional-grade

**Relevance Score**: 10/10 for gabagool

**Notes**: This is the **only source** with full historical order book depth at 1-minute resolution. Critical for backtesting arbitrage execution and simulating fills. Free sample available.

---

## Source 5: Dune Analytics (Free)

**URL**: https://dune.com/

**Type**: Third-party analytics platform

**Data Available**:
- [ ] Order book snapshots
- [x] Trade history (OrderFilled events)
- [x] Market metadata (via LiveFetch)
- [x] 15-minute market coverage
- [x] Historical depth: Full on-chain history

**Access**:
- Method: SQL queries
- Cost: Free tier available, paid for higher limits
- Rate limits: Query-based

**Notable Dashboards**:
- Polymarket - Activity and Volume
- Polymarket Trade Activity Tracker
- Polymarket On-Chain Market Analyzer

**Quality**: Medium-High (requires query building)

**Relevance Score**: 6/10 for gabagool

**Notes**: Tracking the OrderFilled event will give us the volume of each bet. One approach to get data from Polymarket is to check only onchain data related to markets. Good for analysis, but requires SQL knowledge and doesn't provide order book depth.

---

## Source 6: Bitquery (Free/Paid)

**URL**: https://docs.bitquery.io/docs/examples/polymarket-api/

**Type**: Third-party blockchain data

**Data Available**:
- [ ] Order book snapshots
- [x] Trade history (OrderFilled events)
- [x] Market metadata
- [x] 15-minute market coverage
- [x] Historical depth: Full on-chain

**Access**:
- Method: GraphQL API
- Cost: Free tier + paid plans

**Quality**: High

**Relevance Score**: 6/10 for gabagool

**Notes**: Query OrderFilled events from CTF Exchange. Price = USDC paid / tokens received. Similar to Dune but with GraphQL interface.

---

## Source 7: FinFeedAPI (Paid)

**URL**: https://www.finfeedapi.com/products/prediction-markets-api

**Type**: Third-party aggregator

**Data Available**:
- [x] Order book snapshots (current)
- [x] Trade history
- [x] OHLCV candles
- [x] 15-minute market coverage
- [ ] Historical order book depth (unclear)

**Access**:
- Method: REST API
- Cost: Paid (pricing not listed)

**Quality**: Unknown

**Relevance Score**: 5/10 for gabagool

**Notes**: Access Polymarket, Kalshi, Myriad, and Manifold data through a single API instead of maintaining separate connections. Aggregates multiple prediction markets but unclear on historical depth.

---

## Source 8: Kaggle Dataset (Free)

**URL**: https://www.kaggle.com/datasets/sandeepkumarfromin/full-market-data-from-polymarket

**Type**: Community dataset

**Data Available**:
- Unknown (requires Kaggle login)

**Quality**: Unknown

**Relevance Score**: 3/10 (likely outdated)

---

## Comparison Table

| Source | Order Book Depth | Trade History | 15-Min Markets | Cost | Effort |
|--------|------------------|---------------|----------------|------|--------|
| **CLOB API** | ❌ Current only | ✅ Yes | ✅ Yes | Free | Low |
| **Goldsky Subgraph** | ❌ No | ✅ Yes | ✅ Yes | Free | Medium |
| **warproxxx/poly_data** | ❌ No | ✅ Yes | ✅ Yes | Free | Low |
| **PolymarketData.co** | ✅ **Full history** | ✅ Yes | ✅ Yes | Paid | Low |
| **Dune Analytics** | ❌ No | ✅ Yes | ✅ Yes | Free | High |
| **Bitquery** | ❌ No | ✅ Yes | ✅ Yes | Free/Paid | Medium |
| **FinFeedAPI** | ❓ Unclear | ✅ Yes | ✅ Yes | Paid | Low |

---

## Recommendations for Gabagool Backtesting

### Priority 1: Start Free

1. **Clone warproxxx/poly_data** and download their snapshot
   - You're already using warproxxx's poly-maker for the bot
   - Provides processed trade data immediately
   - Good for basic win rate and frequency analysis

2. **Use CLOB API for price history**
   - Query historical prices for 15-min markets
   - Calculate how often `YES_price + NO_price < 0.99` occurred

### Priority 2: Validate with Order Book Depth

3. **Contact PolymarketData.co for sample**
   - Request sample data for 15-minute BTC markets
   - Validate that opportunities detected in price data were actually executable
   - Critical question: When combined < $0.99, was there actually liquidity on both sides?

### Priority 3: Build Your Own Collector

4. **Set up real-time order book logging**
   - Capture live snapshots every minute
   - Build your own historical dataset going forward
   - Use CLOB API's current order book endpoint

---

## Data Gaps to Note

### 15-Minute Markets Are New

Polymarket has launched 15-minute cryptocurrency prediction markets powered by Chainlink's decentralized oracles. These markets launched in late 2025, so historical data is limited to ~3-4 months at most.

### Order Book Depth Is Rare

Most free sources only provide:
- Trade executions (after the fact)
- Price snapshots (midpoint, not depth)

**Only PolymarketData.co offers full bid/ask depth history**, which is essential for:
- Simulating realistic fill prices at various sizes
- Understanding leg risk (price movement between YES and NO orders)
- Calculating actual executable spread vs theoretical spread

---

## Quick Start: Getting Data Today

```bash
# 1. Clone warproxxx's data repo
git clone https://github.com/warproxxx/poly_data.git
cd poly_data

# 2. Download their snapshot (saves days of scraping)
# Link in their README

# 3. Run to get latest data
uv sync
uv run python update_all.py

# 4. Analyze trades.csv for 15-minute markets
# Filter by market_slug pattern: *-updown-15m-*
```

---

## Cost-Benefit Summary

| Approach | Cost | Data Quality | Effort | Recommendation |
|----------|------|--------------|--------|----------------|
| Free sources only | $0 | Trade data, no depth | Medium | Start here |
| Free + PolymarketData sample | $0 | Validate with depth | Low | Request sample |
| PolymarketData subscription | $? | Full professional data | Low | If serious about backtesting |
| Build own collector | $0 + time | Future data only | High | Long-term solution |

---

## Next Steps

1. **Immediate**: Clone warproxxx/poly_data, download snapshot
2. **This week**: Query CLOB API for 15-min market price history
3. **If needed**: Contact PolymarketData.co for order book sample
4. **Ongoing**: Set up your own order book snapshot collector

For gabagool live trading, you may not need extensive backtesting—the math is guaranteed. Historical data is more useful for:
- Estimating opportunity frequency
- Understanding typical spreads
- Validating execution assumptions

# Prompt: Search for Polymarket Historical Order Book Data Sources

## Context for Claude

You are helping research sources of historical order book data for Polymarket prediction markets, specifically for 15-minute crypto price prediction markets.

### What is Polymarket?
- Polymarket is a decentralized prediction market platform on Polygon blockchain
- Users trade YES/NO shares on event outcomes (prices 0.00-1.00)
- When event resolves, winning shares pay $1.00, losing shares pay $0.00
- Trading uses a CLOB (Central Limit Order Book) model

### What are 15-minute markets?
- Short-duration markets predicting if crypto prices go UP or DOWN in 15 minutes
- Assets: BTC, ETH, SOL, XRP (possibly MSTR, DOGE, others)
- New market every 15 minutes (e.g., "Will BTC go up between 04:00-04:15 UTC?")
- Two tokens per market: UP token and DOWN token
- Market slugs follow pattern: `{coin}-updown-15m-{unix_timestamp}`

### What data do we need?
1. **Order book snapshots**: Bid/ask prices and sizes at multiple levels
2. **Trade history**: Executed trades with timestamps, prices, sizes
3. **Market metadata**: Resolution outcomes, volumes, liquidity
4. **Tick-level data**: Sub-second snapshots if available

### Known API endpoints (for reference)
- Gamma API: `https://gamma-api.polymarket.com` - Market discovery, metadata
- CLOB API: `https://clob.polymarket.com` - Order book, trading
- Subgraph: Polymarket has a Graph Protocol subgraph for on-chain data

---

## Your Task

Search for and evaluate sources of historical Polymarket order book data. For each source found, provide:

1. **Source name and URL**
2. **Data available**: What fields, time range, granularity
3. **Access method**: API, download, paid service, etc.
4. **Cost**: Free, subscription, one-time purchase
5. **Quality assessment**: Completeness, reliability, format
6. **Relevance**: How well it matches our needs (15-min markets, order book depth)

---

## Specific Search Queries to Try

### Primary searches:
1. "Polymarket historical data API"
2. "Polymarket order book data download"
3. "Polymarket CLOB historical snapshots"
4. "Polymarket data provider"
5. "Polymarket subgraph historical trades"

### Secondary searches:
6. "Polymarket research data academic"
7. "prediction market historical data Polygon"
8. "Polymarket backtesting data"
9. "CLOB order book data crypto prediction markets"
10. "Polymarket Dune Analytics dashboard"

### Technical searches:
11. "Polymarket Graph Protocol subgraph"
12. "site:github.com polymarket data"
13. "site:dune.com polymarket"
14. "Polymarket Flipside Crypto"

---

## Potential Data Sources to Investigate

### 1. Official Polymarket
- Do they offer historical data exports?
- Is there a data API beyond real-time?
- Any research/academic data programs?

### 2. Blockchain Data Providers
- **Dune Analytics**: SQL queries on Polygon data
- **Flipside Crypto**: Polymarket-specific tables?
- **The Graph**: Polymarket subgraph for on-chain events
- **Nansen / Arkham**: May track Polymarket activity

### 3. Third-Party Data Services
- **Kaiko, CoinAPI, CryptoCompare**: Do they cover prediction markets?
- **DataBento, Tardis**: High-frequency crypto data providers

### 4. Academic/Research
- Any researchers publishing Polymarket datasets?
- Prediction market research papers with data supplements?

### 5. Community/Open Source
- GitHub repositories with scraped data
- Kaggle datasets
- Discord/Telegram communities sharing data

### 6. Archive Services
- Internet Archive / Wayback Machine API snapshots
- Any blockchain archival services

---

## Output Format

Please structure your findings as:

```markdown
## Source: [Name]

**URL**: [link]
**Type**: [Official API / Third-party / Community / Research]
**Data Available**:
- [ ] Order book snapshots
- [ ] Trade history
- [ ] Market metadata
- [ ] 15-minute market coverage
- [ ] Historical depth (how far back)

**Access**:
- Method: [API / Download / Query]
- Cost: [Free / Paid - $X]
- Rate limits: [if applicable]

**Data Format**: [JSON / CSV / Parquet / SQL]
**Granularity**: [Tick / Second / Minute / Hourly]
**Quality**: [High / Medium / Low]
**Notes**: [Any relevant details]

**Relevance Score**: [1-10] for our use case
```

---

## Priority Order

1. **Highest priority**: Full order book snapshots for 15-minute markets
2. **High priority**: Trade-level data with timestamps
3. **Medium priority**: OHLCV or aggregated price data
4. **Lower priority**: General market metadata without depth

---

## Additional Context

We are building an arbitrage monitoring system. The arbitrage opportunity exists when:
- `UP_best_ask + DOWN_best_ask < 1.00` (buy both sides for less than guaranteed payout)

To properly backtest and assess risk, we need:
- Order book depth (to simulate fills at various sizes)
- High-frequency snapshots (to model leg risk - price movement between trades)
- Sufficient history (weeks/months to capture various market conditions)

Current monitoring shows opportunities at Combined=0.99 occur ~0.2% of the time. We need historical data to validate this frequency and assess execution risk.

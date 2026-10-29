# ASX opportunity profile — first run

## Objective

Build a repeatable, evidence-led shortlist of ASX companies with improving
business quality, favourable macro or thematic exposure, and observable price
strength. This is a research and ranking workflow, not a promise to identify
winners or a personalised buy/sell instruction.

## Coverage universe

The first run should maintain separate cohorts so large companies do not hide
micro-cap behaviour:

| Cohort | Initial scope | Purpose |
| --- | --- | --- |
| Market benchmarks | All Ordinaries, ASX 20, 50, 100, 200, 300, MidCap 50, Small Ordinaries, Emerging Companies | Regime, breadth and relative-strength context |
| Broad ASX | Every currently listed ordinary equity that passes a valid ASX symbol and price-history check | Discover companies outside headline indices |
| Micro-cap | Broad-ASX companies below the configured market-cap threshold, with a separate liquidity and dilution review | Find early-stage opportunities without confusing illiquidity with accumulation |
| Sector/theme cohorts | Energy, materials, gold, critical minerals, technology, health, industrials, defence, infrastructure, automation and other GICS groups | Identify macro/geopolitical and innovation beneficiaries |

Index membership and market-cap cohorts must be dated snapshots. Current
membership must never be silently applied to historical performance; that
creates survivorship bias.

## First-run company profile

Each company profile should contain:

- identity: symbol, legal name, exchange, sector, industry and cohort;
- market data: price, adjusted price, volume, turnover, volatility and data age;
- trend: 1-month, 3-month, 6-month and 12-month returns versus the relevant
  benchmark and sector;
- liquidity: median daily turnover, zero-volume days, spread/price-quality
  warning where available and suspension history;
- business quality: revenue, EBITDA/NPAT, margins, cash flow, debt, cash runway,
  return on capital and dilution history when issuer evidence exists;
- catalysts: dated announcements, approvals, contracts, production milestones,
  capital events, director activity and substantial-holder disclosures;
- macro/theme map: commodities, rates, AUD, China demand, energy security,
  geopolitics, regulation and upcoming innovation;
- risk: financing need, concentration, execution, commodity, jurisdiction,
  governance, short interest and missing-evidence flags;
- thesis state: candidate, researching, watchlist, invalidated or archived;
- source register: publisher, URL, publication date, retrieval date, evidence
  tier, confidence and limitations.

## Ranking model

Do not produce a single unexplained winner score. Report component scores:

```text
opportunity = macro_fit + sector_tailwind + business_quality
              + earnings_trend + relative_strength + catalyst_quality
              + accumulation_proxy
              - valuation_risk - liquidity_risk - dilution_risk
              - evidence_gap
```

“Accumulation” is a proxy, not a fact inferred from price alone. The profile
must distinguish volume-supported strength, substantial-holder disclosures,
short-position changes and director activity from ordinary low-float volatility.

## First-run gates

1. Download or register dated benchmark and listed-security snapshots.
2. Run price and volume history for the broad universe in bounded batches.
3. Exclude or separately flag stale, suspended, zero-volume and insufficient-
   history securities.
4. Rank by cohort and sector before creating a cross-market shortlist.
5. Retrieve issuer evidence only for the top candidates; do not download every
   annual report for every micro-cap in the first pass.
6. Generate a shortlist with reasons, missing evidence, invalidation conditions
   and next research actions.

## Data boundaries

Yahoo public chart data is suitable for an experimental, timestamped price
proxy, not a guaranteed execution feed or complete corporate-action record.
ASX announcements and issuer documents should be used for company events and
fundamentals where permitted. Official economic context should come from the
RBA, ABS and relevant regulators. Any source with unclear terms, rate limits or
incomplete coverage stays manual or disabled until reviewed.

## First experiment output

The first run should produce:

- benchmark regime report;
- sector and theme heatmap;
- top 20 large/mid-cap candidates;
- top 20 small-cap candidates;
- top 20 micro-cap candidates;
- a rejected-data report showing stale, illiquid and incomplete names;
- one-page profiles for the top ten candidates;
- a watchlist with thesis, catalyst, invalidation and review dates.


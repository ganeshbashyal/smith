# Mr Smith baseline evaluation framework

Purpose: evaluate whether Smith is becoming more useful, current, accurate and efficient without confusing model fluency with reliability.

## Evaluation tracks

### 1. Knowledge retrieval

- Does the correct persona retrieve the correct V2 section?
- Are source dates, publishers and review status preserved?
- Does Smith distinguish stable knowledge, current feeds and user-provided evidence?
- Are missing documents reported as unavailable?

### 2. Current-data quality

- Feed freshness by source and expected cadence
- Failed or disabled feeds
- Duplicate and stale articles
- Market snapshot completeness
- Timestamp and timezone correctness

### 3. Finance and ASX

- Baseline coverage and completed-company count
- Technical-data availability
- Fundamental evidence completeness
- Ranking stability between snapshots
- False-positive rate of the multi-factor screen
- Number of promoted companies later rejected during deep dive

### 4. Plug and morning brief

- Queue latency
- Anomaly precision
- Missed meaningful events
- Duplicate alerts
- CPU, RAM and battery impact
- Morning brief usefulness and actionability

### 5. Persona response quality

For each persona, test:

- Correct routing
- Evidence use
- Uncertainty handling
- Safety boundary
- Practical next step
- No invented citations or facts

## Weekly operating rhythm

### Daily

- Capture data
- Record anomalies
- Check feed health
- Save morning and end-of-day outputs

### Twice weekly

- Review five random answers per active persona
- Review all financial or health claims that influenced a decision
- Record false positives, missing evidence and confusing responses

### Weekly

- Freeze a baseline snapshot
- Compare rankings and source freshness
- Review Plug resource usage
- Select the next improvement
- Do not change scoring and evaluation criteria simultaneously

## Scoring rubric

Score each reviewed response from 0 to 3:

- 0: incorrect, unsupported or unsafe
- 1: partially useful but materially incomplete
- 2: useful with minor gaps
- 3: accurate, well-scoped, sourced and actionable

Track accuracy, evidence completeness, freshness, latency and resource cost separately. Never combine them into one opaque score.

## Promotion gates

Smith knowledge or automation changes are promoted only when:

1. The behaviour is reproduced by a test or saved example.
2. The source or evidence boundary is documented.
3. The change does not weaken an existing safety rule.
4. Resource impact is measured.
5. A rollback path exists.

## Decision log fields

Date, persona, input, expected behaviour, observed behaviour, evidence used, defect type, severity, proposed change, test added, result, reviewer and promotion decision.

The first review period should establish the baseline rather than optimise aggressively. The goal is to learn which signals are reliable before increasing automation or adding more model capacity.

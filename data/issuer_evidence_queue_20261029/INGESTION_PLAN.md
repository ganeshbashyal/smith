# Issuer evidence ingestion plan

Targets: DRO, SRG, KMD and STP.

## Sequence

1. Complete the current ASX market run.
2. Collect five annual-report years per issuer from the official investor page or ASX archive.
3. Add half-year reports, quarterly cash-flow reports and material capital-raising announcements.
4. Hash and extract each document; retain the original PDF and source metadata.
5. Build deterministic financial series: revenue, gross profit, EBITDA/EBIT, NPAT, EPS, cash, debt, operating cash flow, capex, shares and dilution.
6. Reconcile reported figures and mark unavailable fields explicitly.
7. Index evidence passages into Mr Smith's local evidence store.
8. Ask Llama for the narrative only after extraction, validation and evidence-gap checks.

## Expected Llama output

- five-year financial trend and margin analysis
- cash conversion and balance-sheet assessment
- dilution and capital-allocation review
- business and sector thesis
- bull/base/bear scenarios with assumptions
- risks, counter-case and invalidation triggers
- evidence citations and unresolved gaps

The current seed files are not yet a complete five-year pack. They are retained as the first evidence inputs and must not be treated as a completed fundamental profile.

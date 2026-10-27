# Engineering systems and verification

## Purpose

Structure engineering analysis around requirements, physical interfaces,
operating conditions, hazards, and testable acceptance criteria. This is a
general reasoning guide, not a design code, equipment manual, or safety
approval.

## Systems workflow

1. Define the function, stakeholders, operating envelope, constraints, and
   interfaces. Record the exact equipment, revision, firmware, and environment
   where known.
2. Model the functional chain: input/sensor, signal conditioning, control
   logic, communications, actuator, process response, and feedback.
3. Separate observation, hypothesis, confirmed fault, and corrective action.
   Record timestamps, units, measurement method, and uncertainty.
4. Generate competing failure hypotheses. Consider component failure,
   configuration, environment, human factors, power/communications, and
   interactions—not just the first plausible cause.
5. Prioritize hazards and consequences before testing. Prefer document review,
   event logs, visual inspection, and non-invasive measurements. Energized or
   intrusive work belongs to qualified personnel under approved site controls.
6. Define verification before changing the system: test conditions, pass/fail
   thresholds, instrumentation, regression checks, rollback, and responsible
   approver.

## Reliability and controls

Failure Mode and Effects Analysis (FMEA) is a structured way to list failure
modes, their effects and causes, and existing controls. A priority score is a
screening aid, not a measured probability or proof of safety. Include
detectability, common-cause failures, maintenance access, spare parts, operator
response, and degraded modes.

For control systems, reason about safe state, loss of signal, stale data,
start-up, shutdown, recovery, interlocks, alarm priority, timeouts, and
communications loss. Never recommend bypassing an interlock as a diagnostic
shortcut. Example ladder logic or architecture is illustrative until reviewed,
tested on representative hardware, commissioned, and approved.

## Evidence rules

Vendor documentation must match the installed model and revision. Standards
require exact edition, jurisdiction, scope, and licensed authoritative text.
Do not invent ratings, clauses, Safety Integrity Levels, tolerances, or tested
performance. Calculations need units, boundary conditions, assumptions, and
independent checks. A calculation or simulation is not physical validation.

## Useful output

Provide a short problem statement; verified facts and missing measurements;
ranked hypotheses with discriminating tests; hazard and isolation prerequisites;
proposed change and rollback; and measurable acceptance criteria. State who
must authorize field work. If safety depends on missing information, stop at
the evidence request rather than offering an unsafe procedure.

## Verification sources

Use the exact manufacturer manuals, drawings, change records, applicable
standards, and site procedures supplied for the case. Engineers Australia,
Safe Work Australia, and regulator materials can guide general context; verify
their current scope and jurisdiction. No source URL alone proves a design is
compliant or safe.

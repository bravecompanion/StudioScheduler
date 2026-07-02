# Dance Studio Schedule Optimization Engine
## Architectural & Functional Specification

### 1. Executive Summary

This document outlines the architecture and operational flow for a scheduling optimization engine tailored to a dance studio environment. The system models scheduling as a Resource-Constrained Project Scheduling Problem (RCPSP) and relies on Google OR-Tools (specifically the CP-SAT solver) implemented in Python.

The core design philosophy is "Schedule First, Register Later." The solver optimizes the curriculum layout and teacher utilization based on spatial and chronological constraints. Student registration occurs downstream of this system's output.

**Scope clarification — this is a curriculum-layout optimizer, not a per-family optimizer.** Because registration happens *after* scheduling, the engine has no knowledge of individual students, siblings, or family groupings. Dancer-continuity goals (keeping a child's classes parallel or back-to-back) are therefore optimized at the **age-group / cohort level as a statistical proxy**, not as a per-student guarantee. The "Parent Taxi" / sibling-gap problem — minimizing parent trips for families with multiple enrolled children — is explicitly **out of scope** for this engine. It can only be addressed by a separate downstream re-solve that ingests actual enrollment and `Family_ID` data, and should be treated as a future phase rather than an expectation of this system.

### 2. The Mathematical Model

#### 2.1 Time Model

The schedule covers a single representative **planning week**. Time is discretized into fixed **epochs** (e.g., one epoch = 10 minutes).

* **Day segmentation:** The week is divided into explicit days (`MON`…`SUN`). Epochs are **namespaced per day** — an epoch is addressed as a `(Day, Slot)` pair and never spans a day boundary — so that no class, gap, or break can straddle overnight. The pair is flattened to a global integer only for solver bookkeeping.
* **Operating windows:** Each day defines an `Open_Epoch` and `Close_Epoch` sourced from a `Calendar` input (§3), not hard-coded.
* **Datetime mapping:** A single anchor (`Week_Anchor_Date` + `Epoch_Minutes`) deterministically converts any `(Day, Slot)` to a wall-clock datetime for output, and back again for input parsing.

#### 2.2 Decision Variables

Rather than a combinatorial boolean tensor, each class is represented by an interval plus resource assignments. Resource exclusivity uses the **optional-interval decomposition** — the idiomatic CP-SAT pattern for "assign one of N resources without overlap" — because exclusivity cannot be expressed directly from a plain integer `RoomVar`/`TeacherVar`:

* `IntervalVar` (per class session): the primary time block, linking start, fixed duration, and end.
* **Optional room intervals** (per class × candidate room): a set of *optional* `IntervalVar` copies, exactly one of which is enforced present. `AddNoOverlap` is applied **per room** across all classes' optional copies.
* **Optional teacher intervals** (per class × candidate teacher): the same construction, with `AddNoOverlap` applied **per teacher**.

Derived integer `RoomVar` / `TeacherVar` are retained only as convenience variables, channeled from the active optional interval via its presence literal, for use in capacity, attribute, and right-sizing expressions.

#### 2.3 The Objective Function

The solver minimizes a single **integer** objective: the weighted sum of all soft-constraint penalty terms (§4). Because CP-SAT requires integer objectives, every term is integer-scaled and rewards are expressed as bounded negative penalties.

* **Commensurability:** Each term is normalized to a common scale *before* weighting so no single term silently dominates due to unit differences (epochs vs. age-deltas vs. hours). Weights live in external config (§3, §5).
* **Bounded rewards:** Continuity rewards (parallel/sequential) are capped per eligible class pair so the solver cannot manufacture unbounded negative cost.

### 3. Data Dictionary (Static Inputs)

Data is strictly segregated from the solver's internal variables. Inputs are ingested via structured files (e.g., CSV) and parsed into native data classes. A **preprocessing layer** converts human-friendly inputs (day/time ranges, attribute lists, highest-level qualifications) into the integer epoch space before the model is built.

#### Calendar / Studio

* `Day`: A day the studio operates (`MON`…`SUN`).
* `Open_Epoch` / `Close_Epoch`: Operating window for that day, in epochs.
* `Epoch_Minutes`: Length of one epoch (global constant, e.g., 10).
* `Week_Anchor_Date`: Calendar date of the planning week's first day, used for datetime conversion on output.

#### Rooms

* `Room_ID`: Unique string/integer identifier.
* `Capacity`: Maximum student occupancy (Integer).
* `Attributes`: A boolean attribute map of the room's fixed features (e.g., `Floor=SPRUNG`, `Has_Barre=true`, `Has_Mirrors=true`). Replaces the single `Floor` field so requirements beyond flooring (barres for ballet/pointe, mirrors, etc.) can be matched generically.

#### Cohorts (Age Group Mapping)

* `Age_Group`: Unique identifier for the cohort (e.g., `TINY_TOTS`, `MINI`, `JUNIOR`, `TEEN`).
* `Min_Age` / `Max_Age`: Integer bounds defining the target demographic. This mapping is used by the preprocessing layer so age bounds don't need to be repeated on every class. Used for the Early-Bird penalty and reporting.

#### Classes

* `Class_ID`: Unique string/integer identifier.
* `Type`: Subject category (e.g., `BALLET`, `TAP`, `CONTEMPORARY`, `HIPHOP`, `JAZZ`, `POINTE`).
* `Age_Group`: The cohort identifier this class belongs to. The solver pulls `Min_Age` and `Max_Age` from the Cohorts mapping via this key. Two classes share a cohort only if their `Age_Group` matches.
* `Class_Size`: Anticipated enrollment (Integer). **Note:** under "Schedule First, Register Later" this is an *estimate*; capacity and right-sizing are optimized against a forecast, not actuals.
* `Duration_Minutes`: Length of one session in minutes. The preprocessing layer divides this by `Epoch_Minutes` to get the internal `Duration_Epochs`.
* **Pinning Fields (Optional):** `Pinned_Time`, `Pinned_Room_ID`, `Pinned_Teacher_ID`. Any subset may be set independently to force assignments prior to the search. `Pinned_Time` (e.g., "MON 17:00") is converted to `Pinned_Epoch` internally.

#### Teachers

* `Teacher_ID`: Unique string/integer identifier.
* `Qualifications`: Allowed `Type:Skill_Level` pairings. Qualifications are **hierarchical**: authorization at a level implies authorization for all lower levels of the same `Type` (e.g., `BALLET:ADVANCED` ⇒ `BALLET:INTERMEDIATE`, `BALLET:BEGINNER`). Expanded during preprocessing so data entry lists only the highest level.
* `Availability`: Human-entered day/time ranges of **hard unavailability** (e.g., "MON before 16:00"). Preprocessing converts these to the internal `Unavailable_Epochs` set. Captures hard blocks only; preferences are expressed via soft terms.
* `Max_Consecutive_Epochs`: Limit on back-to-back teaching before a mandatory break (see Labor Limits, §4).
* `Target_Hours`: Soft target for weekly workload.

#### Configuration / Weights

* A separate `config.json` holds every soft-constraint **weight**, the **gap-penalty buckets** (§4, term 4), per-term normalization scales, and solver settings (time limit, worker count, random seed). Tuning happens here, never in code.

### 4. Constraints (The Logic Engine)

Constraints govern the relationships between the decision variables.

#### Hard Constraints (Infeasibility Triggers)

* **Operating Window:** Every class interval must lie entirely within its day's `[Open_Epoch, Close_Epoch)` window and may not cross a day boundary.
* **Spatial Exclusivity:** Enforced via `AddNoOverlap` over each room's optional class intervals (§2.2); at most one class occupies a room at any epoch.
* **Staff Exclusivity:** Enforced via `AddNoOverlap` over each teacher's optional class intervals; a teacher is never double-booked.
* **Capacity Limit:** The active room's `Capacity` $\ge$ the class's `Class_Size` (estimate). Enforced by restricting each class's candidate-room set during pre-filtering.
* **Attribute Match:** The active room's `Attributes` must satisfy every entry in the class's `Required_Attributes`. Applied during pre-filtering, so non-matching rooms never enter the domain.
* **Qualification Match:** The active teacher must hold the class's required `Type:Skill_Level` after hierarchical expansion. Applied during pre-filtering.
* **Multi-Session Separation:** The `Sessions_Per_Week` instances of a class must fall on distinct days (a non-adjacent-day preference is applied as soft term 7).

> **Implementation note — Labor Limits reclassified.** Enforcing "consecutive teaching time $\le$ `Max_Consecutive_Epochs`" *exactly* requires reasoning over chains of back-to-back assignments per teacher, which is expensive and brittle. For v1 it is implemented as a **soft** penalty (or an approximate per-window load cap) — see soft term 8 — and only promoted to a hard constraint if a studio requires it.

#### Soft Constraints & Objective Function (Minimization Targets)

All terms are integer-scaled and normalized to a common scale before weighting (§2.3). Rewards are bounded negative penalties.

1. **Chronological Age Weighting (Early Bird):** Younger cohorts are penalized for starting later in the day. By using the studio's daily opening time as the baseline, we avoid needing to manually define an "ideal" start time per class.
* $\text{Penalty} = (\text{Start\_Epoch} - \text{Open\_Epoch}) \times \text{Age\_Weight}$
* $\text{Age\_Weight} = \max(0,\ \text{AGE\_PIVOT} - \text{Min\_Age})$, where `AGE_PIVOT` is a config constant. This makes the penalty multiplier **larger for younger** students and zeroes it out entirely for older students.

2. **Parallel Scheduling Reward:** For class pairs of the same `Type` and `Age_Group` but different `Skill_Level`, reward simultaneous starts (mixing levels within a cohort while preserving per-dancer continuity).
* If $\text{Start}(C_1) = \text{Start}(C_2)$, apply a **bounded** reward. Pairs are pre-filtered so only eligible same-cohort pairs create reified variables, avoiding the $O(n^2)$ blow-up.

3. **Sequential Scheduling Weighting:** For class pairs of the same `Age_Group` but different `Type` (e.g., Tap after Ballet), reward contiguity and forbid same-cohort overlap.
* Bounded reward if $\text{End}(C_1) = \text{Start}(C_2)$ (or vice-versa); heavy penalty if same-cohort intervals overlap. *Proxy caveat:* this assumes a typical dancer in the cohort takes both classes; it cannot guarantee any individual's schedule.

4. **Teacher Gap Penalty (bucketed):** For classes assigned to the same teacher on the same day, the gap between consecutive classes is penalized **non-linearly** via configurable buckets — encoding that one 90-minute gap is far worse than two 15-minute gaps.
* Each realized gap is mapped to an integer **bucket index** with an associated penalty (from `config.json`), rather than a linear `gap × weight`. A representative map: `0` → 0 · small gap → low · **mid gap → punishing** ("too big to teach, too small to go home") · meal-length gap → low or slightly negative (a desirable break) · very large gap → moderate.

5. **Workload Optimization:** Penalize the absolute deviation of each teacher's scheduled hours from `Target_Hours`.

6. **Room Right-Sizing:** Minimize wasted capacity, $\text{Capacity}(\text{room}) - \text{Class\_Size}$, via an element expression over the active room. Keeps large studios free for classes that need them.

7. **Multi-Session Day Spread:** Penalize placing a class's multiple weekly sessions on adjacent days (encourages e.g. Mon/Thu over Mon/Tue).

8. **Labor Smoothness (was hard Labor Limit):** Penalize runs of consecutive teaching beyond `Max_Consecutive_Epochs`. Soft in v1 (see note above).

9. **Variety of Start Times:** For classes of the same `Type` and `Age_Group`, stagger start times. Penalize assigning identical or highly similar start times across the week, maximizing scheduling options for dancers.

10. **Variety of Teachers:** For classes of the same `Type` and `Age_Group`, reward distributing the assignments across different teachers to ensure students have a choice of instructors.

11. **Traffic Smoothing (Hallway/Parking Logistics):** Penalize having many classes start at the exact same epoch globally across the studio. This flattens the peak load of simultaneous arrivals and departures.

12. **Late Session Guarantee:** For each cohort (`Type` and `Age_Group`), heavily penalize if *no* session is scheduled after a designated late-afternoon threshold (e.g., 16:00 / 4:00 PM).

13. **Substitute Availability — DEFERRED (post-MVP):** Reward schedules where, for each class, another qualified teacher is free during that slot (resilience to call-outs). This term reasons counterfactually over all other assignments and is the most expensive in the model; it is **excluded from v1** and revisited once the core solves comfortably within the time budget.

### 5. Operational Workflow

1. **Pre-Flight Validation:** Before building the model, the system runs cheap feasibility and integrity checks and fails fast with a human-readable report instead of a bare `INFEASIBLE`. Checks include: every class has $\ge 1$ qualified teacher and $\ge 1$ attribute-and-capacity-compatible room; total qualified teacher-hours cover demand per `Type:Skill_Level`; pinned IDs exist and are mutually consistent; required attributes are satisfiable by some room; demand fits within aggregate operating-window capacity.
2. **Pre-Search Filtering:** The parser pre-filters each class's candidate room and teacher sets (capacity, attributes, hierarchical qualifications), so only viable optional intervals are created — heavily pruning the search tree.
3. **Variable Pinning:** Any class with `Pinned` fields is locked via strict equality before the search (e.g., `Start_Epoch == Pinned_Epoch`). Subsets may be pinned independently.
4. **Execution:** CP-SAT runs until optimality, a wall-clock timeout, or infeasibility. For **reproducibility**, the run fixes `random_seed`, pins `num_search_workers`, and applies a deterministic tie-break objective so equal-cost schedules do not reshuffle between runs. (Without this, parallel workers and timeouts can return different optimal-but-different schedules, undermining the version-controlled-inputs audit story.)
5. **Tuning:** Soft-constraint weights and gap buckets live in `config.json`; iteration happens there. The penalty **scorecard** (§6) shows which terms dominate so tuning is informed rather than blind.

### 6. Output Artifacts

The engine emits **deterministic** outputs (given the fixed seed/worker/tie-break settings of §5) for downstream database systems or CSV export.

#### Primary Assignment Table

A flat list of mapped assignments, one row per class session:

* `Class_ID`
* `Session_Index` (for multi-session classes)
* `Assigned_Day`
* `Assigned_Start_Epoch` (converted back to standard datetime via `Week_Anchor_Date`)
* `Assigned_Room_ID`
* `Assigned_Teacher_ID`

#### Penalty Scorecard

A per-term breakdown of the objective (e.g., `early_bird: 120`, `teacher_gap: 45`, `right_sizing: 30`, `parallel_reward: -60`) plus the total. Required for informed weight tuning (§5) and provides the before/after metrics for evaluating a schedule.

#### Solve Report

Status (`OPTIMAL` / `FEASIBLE` / `INFEASIBLE`), wall-clock time, objective value and best bound, and — on infeasibility — the failing pre-flight checks (§5, step 1) that explain *why* no schedule exists.

### 7. Implementation Scope (Definition of Ready)

The following are locked for **v1**; the rest are explicitly deferred.

**In scope (v1):**
* Time model with per-day epochs and operating windows (§2.1).
* Optional-interval decomposition for room/teacher exclusivity (§2.2).
* Hard constraints: operating window, spatial/staff exclusivity, capacity, attribute match, qualification match, multi-session separation.
* Soft terms: Early Bird (fixed formula), Parallel reward, Sequential weighting, bucketed Teacher Gap, Workload, Room Right-Sizing, Multi-Session Day Spread, Labor Smoothness, Start Time Variety, Teacher Variety, Traffic Smoothing, Late Session Guarantee.
* Pre-flight validation, penalty scorecard, solve report.
* Deterministic solver configuration.

**Deferred (post-MVP):**
* Substitute Availability (soft term 9).
* Labor Limits as a *hard* constraint (soft smoothness only in v1).
* Family/sibling ("Parent Taxi") optimization — requires a downstream enrollment-aware re-solve (§1).
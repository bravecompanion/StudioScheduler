# Dance Studio Schedule Optimization Engine
## Architectural & Functional Specification

### 1. Executive Summary

This document outlines the architecture and operational flow for a scheduling optimization engine tailored to a dance studio environment. The system models scheduling as a Resource-Constrained Project Scheduling Problem (RCPSP) and relies on Google OR-Tools (specifically the CP-SAT solver) implemented in Python.

The core design philosophy is "Schedule First, Register Later." The solver optimizes the curriculum layout and teacher utilization based on spatial and chronological constraints. Student registration occurs downstream of this system's output.

### 2. The Mathematical Model

The problem is modeled using discrete time periods (epochs) mapped to a unified integer grid (e.g., one epoch = 10 minutes).

* **The Decision Variables:**
Instead of a combinatorial boolean tensor, the solver manipulates three primary variables per class:
* `IntervalVar`: Defines the specific time block the class occupies, inherently linking the start time, fixed duration, and end time.
* `RoomVar`: An integer variable representing the assigned room index.
* `TeacherVar`: An integer variable representing the assigned teacher index.

* **The Solver's Goal:**
Find a valid assignment of intervals, rooms, and teachers that satisfies all Hard Constraints, while minimizing the total penalty score of the Objective Function (Soft Constraints).

### 3. Data Dictionary (Static Inputs)

Data is strictly segregated from the solver's internal variables. Inputs are ingested via structured files (e.g., CSV) and parsed into native data classes.

#### Rooms

* `Room_ID`: Unique string/integer identifier.
* `Capacity`: Maximum student occupancy (Integer).
* `Floor`: Required surface (Enum/String).

#### Classes

* `Class_ID`: Unique string/integer identifier.
* `Type`: Subject category (e.g., `BALLET`, `TAP`, `CONTEMPORARY`, `HIPHOP`, `JAZZ`, `POINTE`).
* `Skill_Level`: Progression tier (e.g., `BEGINNER`, `INTERMEDIATE`, `ADVANCED`, `NA`).
* `Min_Age` / `Max_Age`: Integer bounds defining the target demographic.
* `Class_Size`: Anticipated or maximum enrollment (Integer).
* `Duration_Epochs`: Length of the class calculated in base time units.
* `Floor_Req`: Required floor.
* **Pinning Fields (Optional):** `Pinned_Epoch`, `Pinned_Room_ID`, `Pinned_Teacher_ID`. Used to manually force assignments prior to the search.

#### Teachers

* `Teacher_ID`: Unique string/integer identifier.
* `Qualifications`: Array of allowed `Type:Skill_Level` pairings.
* `Unavailable_Epochs`: Array of integers representing hard block-outs.
* `Max_Consecutive_Epochs`: Integer limit before a mandatory break is required.
* `Target_Hours`: Soft target for weekly workload.

### 4. Constraints (The Logic Engine)

Constraints govern the relationships between the decision variables.

#### Hard Constraints (Infeasibility Triggers)

* **Spatial Exclusivity:** No two class `IntervalVar` assignments sharing the same `RoomVar` can overlap in time.
* **Staff Exclusivity:** No two class `IntervalVar` assignments sharing the same `TeacherVar` can overlap in time.
* **Capacity Limit:** Assigned room `Capacity` must be $\ge$ class `Class_Size`.
* **Hardware Match:** Assigned room `Floor` must strictly equal class `Floor_Req`.
* **Qualification Match:** Assigned `TeacherVar` must possess the exact `Type:Skill_Level` required by the class.
* **Labor Limits:** Total consecutive teaching time without a gap must be $\le$ teacher's `Max_Consecutive_Epochs`.

#### Soft Constraints & Objective Function (Minimization Targets)

The solver evaluates and sums the following penalties to find the mathematically optimal schedule.

1. **Chronological Age Weighting (Early Bird):** Younger age groups are penalized for late start times.
* $\text{Penalty} = (\text{Start\_Epoch} - \text{Ideal\_Epoch}) \times (\text{Age\_Weight} - \text{Min\_Age})$


2. **Parallel Scheduling Reward:** Incentivizes simultaneous scheduling for classes of the exact same `Type` and `Min_Age`, but different `Skill_Level`--allows mixing skill levels in an age group while maintaining per-dancer schedule continuity.
* If $\text{Start}(C_1) == \text{Start}(C_2)$, apply negative penalty (reward).


3. **Sequential Scheduling Weighting:** Incentivizes contiguous, non-overlapping scheduling for classes of the same `Min_Age` but different `Type` (allowing a student to take Tap directly after Ballet).
* Reward if $\text{End}(C_1) == \text{Start}(C_2)$. Heavily penalize if $\text{Interval}(C_1)$ and $\text{Interval}(C_2)$ overlap.


4. **Teacher Gap Penalty:** Penalizes fragmented teaching schedules.
* For classes assigned to the same teacher on the same day: $\text{Penalty} = (\text{Start}(B) - \text{End}(A)) \times \text{Gap\_Weight}$.
  * NOTE TO SELF: different-size gaps may have different penalties (i.e., "big but not big enough to go home")--meal breaks might nave a negative penalty

5. **Workload Optimization:** Penalizes deviation from a teacher's `Target_Hours`.

6. **Substitute Availability Penalty:** Penalize schedules where no substitude (additional teacher with the same `Type:Skill_Level` capability) is free

7. **Room Right-Sizing:** Minimize wasted capacity

### 5. Operational Workflow

1. **Pre-Search Filtering:** The system parses input data. Instead of loading every teacher into every class's variable domain, it pre-filters `TeacherVar` domains strictly to qualified candidates, heavily pruning the search tree.
2. **Variable Pinning:** Any class with `Pinned` fields in the input data is locked via strict equality constraints (e.g., `Start_Epoch == Pinned_Epoch`) before the solver initiates.
3. **Execution & Tuning:** The CP-SAT solver runs until it hits an optimal state, a wall-clock timeout limit, or an infeasibility flag. Soft constraint weights are maintained in an external configuration file to allow iterative human tuning without code changes.

### 6. Output Artifacts

The engine generates a strictly deterministic, flat intermediary output that can be utilized by downstream database systems or CSV exports.

The standard output object is a flat list of mapped assignments:

* `Class_ID`
* `Assigned_Start_Epoch` (converted back to standard datetime)
* `Assigned_Room_ID`
* `Assigned_Teacher_ID`
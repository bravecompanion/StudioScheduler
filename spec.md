# Dance Studio Schedule Optimization Engine
## Architectural & Functional Specification

### 1. Executive Summary
This document outlines the architecture and operational flow for a scheduling optimization engine tailored to a dance studio environment (pilot scale: ~500 students). The system frames scheduling as a Constraint Satisfaction Problem (CSP) and relies on Google OR-Tools (specifically the CP-SAT solver) implemented in Python. 

The core design philosophy utilizes an "Orthogonal State and Behavior" paradigm. The solver (behavior) is strictly decoupled from the studio data and constraints (state).

### 2. The Mathematical Model
The problem is modeled by mapping events to resources over discrete time intervals.

* **The Decision Variable (Dynamic State):**
    The solver manipulates a multidimensional boolean tensor.
    Let `x[c, t, r, p]` be a boolean variable that equals `1` if Class `c` is taught by Teacher `t` in Room `r` at Time Period `p`, and `0` otherwise.
* **The Solver's Goal:**
    Find a valid combination of 1s and 0s that satisfies all Hard Constraints, while minimizing the penalty score of all Soft Constraints.

### 3. Data Dictionary (Static Attributes)
Entities in this system act as pure IDs mapped to immutable data components. This data must be ingested via strictly formatted CSVs or an API connection to existing studio software.

* **Rooms:**
    * `Capacity`: Maximum occupancy.
    * `FloorType`: Required surface (e.g., sprung wood, marley).
* **Teachers:**
    * `Qualifications`: Boolean map of authorized class types (e.g., Ballet, Pointe).
    * `Availability`: Hard un-availability blocks.
    * `Preferences`: Target weekly hours, shift continuity preferences.
* **Classes/Curriculum:**
    * `Type/Level`: Subject and skill level.
    * `Duration`: Time required (e.g., 45 mins, 60 mins).
    * `Hardware Requirements`: Required room attributes.
* **Students/Families:**
    * `Family ID`: Grouping parameter for siblings.
    * `Enrollment`: Requested classes per student.

### 4. Constraints
Constraints dictate the rules the solver must follow.

#### Hard Constraints (Must Satisfy)
* **Resource Uniqueness:** A teacher can only be in one room at a time.
* **Room Exclusivity:** A room can only host one class at a time.
* **Equipment Matching:** Class hardware requirements must match the assigned room's attributes.
* **Qualifications:** Teachers can only be assigned to classes they are qualified to teach.

#### Soft Constraints / Cost Function (Minimize Violations)
* **The "Parent Taxi" Penalty:** High penalty for scheduling gaps between classes taken by siblings (linked via Family ID).
* **Teacher Gap Penalty:** Penalty for fragmented teaching schedules (e.g., 1-hour class, 2-hour gap, 1-hour class).
* **Age/Level Sequencing:** Preference for chronological flow (younger students earlier in the day, advanced students later).

### 5. Operational Workflow & Iteration
Schedule refinement is achieved by adjusting inputs and re-running the solver, never by manually editing the output files. This ensures reproducibility.

* **Variable Pinning (Partial Assignments):** If a specific class must happen at a specific time/room, it is hardcoded via an input CSV prior to the solver run. The solver works around this pinned variable.
* **Constraint Weight Tuning:** Soft constraints are exposed in a configuration file (e.g., `config.json`). To fix a specific bad outcome (like too much parent waiting), the corresponding penalty weight is increased.
* **Version Control:** Input CSVs and configuration files are tracked via Git. "What-if" scenarios are tested on separate branches to provide an audit trail of how data changes affect the final schedule.

### 6. Output Artifacts & Visualization
The engine generates deterministic, machine-readable outputs that serve dual purposes: system integration and human visualization.

#### File Formats
* `.csv` (Comma-Separated Values): Used for bulk ingest back into studio management software and flat-file storage.
* `.ics` (iCalendar): Generated for specific individuals (teachers, parents) to allow one-click syncing to personal calendar apps.

#### Visualization Strategy (Dimensional Slicing)
Because a single master calendar cannot elegantly display multidimensional constraints, the data is pivoted into specific operational views:
1.  **The Resource Gantt (Room Slice):** Y-Axis = Rooms, X-Axis = Time. Used to visually verify spatial utilization and absence of double-bookings. (Artifact: Master Studio View).
2.  **The Swimlane View (Teacher Slice):** Y-Axis = Teachers, X-Axis = Time. Used to check for burnout and schedule gaps. (Artifact: Individual Instructor Schedule).
3.  **The Cohort Matrix (Student/Family Slice):** Y-Axis = Cohort/Family, X-Axis = Time. Used to verify the continuous block of classes for the "Parent Taxi" constraint. (Artifact: Curriculum Track).

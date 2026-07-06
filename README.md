# Studio Scheduler

Studio Scheduler is a constraint-programming optimization engine that generates conflict-free schedules for a dance or fitness studio. It assigns classes to specific rooms and teachers while preventing double-booking, and uses Google's OR-Tools (CP-SAT solver) to find configurations based on hard and soft constraints.

## Architecture

The project consists of three primary components:
1. **Data Pipeline (`models.py`, `parser.py`, `exporter.py`)**: Loads class configurations and requirements from a CSV file (`classes.csv`), parses teacher availability and preferences from YAML (`teachers.yaml`), structures them into Python dataclasses, and exports the generated schedule to JSON.
2. **Optimization Engine (`solver.py`)**: Uses CP-SAT to explore combinations concurrently across multiple CPU threads.
3. **Frontend Visualizer (`index.html`)**: A Bootstrap 5 & FullCalendar-based local web app that consumes the exported JSON and provides an interactive visualization of the schedule.

## Class and Teacher Configurations
Inputs are defined in `classes.csv` and `teachers.yaml`. Recent configurations include:
- **Time Windows**: Classes can be constrained by `AllowedDays`, `EarliestStart`, and `LatestEnd`.
- **Teacher Availability**: Teachers have per-day start and end availability times.
- **Teacher Preferences**: Teachers can declare `hate_class` and `hate_cohort` lists, which act as hard constraints preventing those assignments.

## How the Solver Works

The solver engine (`src/solver.py`) is structured via the `StudioSchedulerModel` class.

When `main.py` runs, the `build_and_solve()` function orchestrates the lifecycle:

1. **`create_variables()`**
   - For every class in `classes.csv`, the solver generates possible "start time" variables (epochs).
   - It generates Boolean variables representing whether a class is assigned to a specific room or teacher.

2. **`add_hard_constraints()`**
   - Hard constraints must be satisfied for the schedule to be valid.
   - **Exactly One**: Forces every class to select exactly one valid room and exactly one valid teacher.
   - **No Overlap**: Ensures no two classes occupy the same room simultaneously, and no teacher teaches two classes simultaneously.
   - **Pinning**: Enforces explicit overrides (e.g., if a class is pinned to a specific room or teacher).
   - **Time Windows**: Enforces class time limits and allowed days.
   - **Teacher Minimum Hours**: Ensures teachers have > 1 hour scheduled every day they teach.
   - **Teacher Hates**: Prevents assigning teachers to classes or cohorts they dislike.
   - **Singleton Overlaps**: Prevents overlaps between singleton classes in the same cohort.

3. **`add_soft_constraints()`**
   - Soft constraints apply mathematical "penalties" to specific outcomes. The solver attempts to minimize the total penalty.
   - **Teacher Idle Time**: Penalizes gaps between a teacher's classes on a given day.
   - **Teacher Monopoly**: Hard penalty at 3+ sessions of the same class for a single teacher.
   - **Teacher Diversity**: Gentle penalty for assigning the same teacher to multiple sessions of the same class.
   - **Cohort Clustering**: Rewards scheduling classes of the same cohort close together, and penalizes them for being far apart (pulls singletons to the same day and tightens their schedule).
   - **Session Time Clustering**: Diversifies the time-of-day that sessions of the same class are offered.
   - **Overlapping Sessions**: Penalizes scheduling 2 sessions of the same class at the same time.

4. **`seed_hints()`**
   - Runs a greedy pre-solver to generate a baseline schedule, passing it to CP-SAT via `AddHint()` to accelerate search convergence.

5. **`solve()`**
   - Executes the CP-SAT engine using multi-threading (currently set to 27 workers). Stops early via `PlateauStoppingCallback` if no improvement is found.

## Extending the Solver (Adding Soft Constraints)

To introduce a new scheduling rule:

### Step 1: Write the Constraint Method
Create a new private method in the `StudioSchedulerModel` class inside `src/solver.py`. 

For example, a constraint to penalize classes scheduled on Fridays:
```python
def _penalize_friday_classes(self):
    """Soft Constraint: Minimize classes scheduled on Friday."""
    for c in self.classes:
        if c.id not in self.class_vars: continue
        
        start_var = self.class_vars[c.id]['start']
        # Boolean flag for whether the class lands on a Friday
        is_friday = self.model.NewBoolVar(f'is_friday_{c.id}')
        
        # Calculate the day index (0=MON, 1=TUE, 2=WED, 3=THU, 4=FRI)
        day_idx = self.model.NewIntVar(0, len(self.cal.days) - 1, f'day_idx_{c.id}')
        self.model.AddDivisionEquality(day_idx, start_var, self.cal.day_offset)
        
        # If day_idx == 4, then is_friday = 1
        self.model.Add(day_idx == 4).OnlyEnforceIf(is_friday)
        self.model.Add(day_idx != 4).OnlyEnforceIf(is_friday.Not())
        
        # Add a penalty for any class landing on Friday
        self.penalties.append(is_friday * 100)
```

### Step 2: Register the Constraint
Call your new method inside `add_soft_constraints`:

```python
    def add_soft_constraints(self):
        self._penalize_friday_classes() # <--- Your new constraint!
        
        if self.penalties:
            self.model.Minimize(sum(self.penalties))
```

### Constraint Tips
- **Piecewise Step Functions**: Thresholds can be created using `model.AddMaxEquality()`. For example, "Only penalize this class if its start time goes past 5:00 PM."
- **Variables**: CP-SAT requires modeling decisions as integer variables (`NewIntVar`) or boolean variables (`NewBoolVar`). Standard Python `if/else` logic cannot evaluate class start times during constraint definition. Use OR-Tools mathematical equivalents like `AddModuloEquality` or `.OnlyEnforceIf()`.

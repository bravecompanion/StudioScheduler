# Studio Scheduler

Studio Scheduler is a robust constraint-programming optimization engine designed to automatically generate conflict-free schedules for a dance or fitness studio. It handles assigning classes to specific rooms and teachers while ensuring no double-booking occurs, and it leverages Google's OR-Tools (CP-SAT solver) to find mathematically optimal configurations based on a series of hard and soft constraints.

## Architecture

The project consists of three primary components:
1. **Data Pipeline (`models.py`, `parser.py`, `exporter.py`)**: Loads class configurations and requirements from a CSV file (`classes.csv`), structures them into Python dataclasses, and exports the final generated schedule to JSON.
2. **Optimization Engine (`solver.py`)**: Uses CP-SAT to explore thousands of combinations concurrently across multiple CPU threads to find the most optimal schedule.
3. **Frontend Visualizer (`index.html`)**: A Bootstrap 5 & FullCalendar-based local web app that consumes the exported JSON and provides a highly readable, interactive visualization of the schedule.

## How the Solver Works

The solver engine (`src/solver.py`) is built using a clean, extensible **Object-Oriented Architecture** via the `StudioSchedulerModel` class.

When `main.py` runs, the `build_and_solve()` function orchestrates the following lifecycle:

1. **`create_variables()`**
   - For every class in `classes.csv`, the solver generates possible "start time" variables (epochs).
   - It also generates Boolean variables representing whether a class is assigned to a specific room or teacher.

2. **`add_hard_constraints()`**
   - This lays down the physics of the schedule. Hard constraints *cannot* be broken under any circumstances.
   - **Exactly One**: Forces every class to select exactly one valid room and exactly one valid teacher.
   - **No Overlap**: Ensures that no two classes occupy the same room simultaneously, and no teacher is scheduled to teach two classes simultaneously.
   - **Pinning**: Enforces explicit overrides (e.g., if a class is pinned to "Teacher A" in the CSV, it must be assigned to "Teacher A").

3. **`add_soft_constraints()`**
   - Soft constraints do not make a schedule invalid; rather, they add mathematical "penalties" to specific outcomes. The solver will actively try to minimize the sum of all penalties across the schedule.
   - By encapsulating the model in an Object-Oriented structure, every soft constraint is isolated into its own private method (e.g., `_penalize_late_young_classes()`) which is called from this orchestrator.

4. **`solve()`**
   - Unleashes the CP-SAT engine utilizing multi-threading (currently set to 24 workers) to explore different branches of the decision tree concurrently.

## Extending the Solver (Adding Soft Constraints)

The Object-Oriented structure makes it incredibly easy to add new scheduling logic. If you want to introduce a new preference (e.g., "Teachers prefer not to have gaps between classes"), you follow these three steps:

### Step 1: Write the Constraint Method
Create a new private method in the `StudioSchedulerModel` class inside `src/solver.py`. 

For example, a constraint to heavily penalize classes scheduled on Fridays:
```python
def _penalize_friday_classes(self):
    """Soft Constraint: Minimize classes scheduled on Friday."""
    for c in self.classes:
        if c.id not in self.class_vars: continue
        
        start_var = self.class_vars[c.id]['start']
        # We need a boolean flag for whether the class lands on a Friday
        is_friday = self.model.NewBoolVar(f'is_friday_{c.id}')
        
        # Calculate the day index (0=MON, 1=TUE, 2=WED, 3=THU, 4=FRI)
        day_idx = self.model.NewIntVar(0, len(self.cal.days) - 1, f'day_idx_{c.id}')
        self.model.AddDivisionEquality(day_idx, start_var, self.cal.day_offset)
        
        # If day_idx == 4, then is_friday = 1
        self.model.Add(day_idx == 4).OnlyEnforceIf(is_friday)
        self.model.Add(day_idx != 4).OnlyEnforceIf(is_friday.Not())
        
        # Add a heavy penalty of 100 for any class landing on Friday
        self.penalties.append(is_friday * 100)
```

### Step 2: Register the Constraint
Call your new method inside the `add_soft_constraints` orchestrator:

```python
    def add_soft_constraints(self):
        self._penalize_late_young_classes()
        self._penalize_friday_classes() # <--- Your new constraint!
        
        if self.penalties:
            self.model.Minimize(sum(self.penalties))
```

### Constraint Tips
- **Piecewise Step Functions**: Instead of penalizing something constantly (like age), you can create "Thresholds". We use `model.AddMaxEquality()` to say "Only penalize this class if its start time goes *past* 5:00 PM." This allows the solver flexibility to move things around prior to the deadline without penalty.
- **Variables**: CP-SAT requires you to model decisions as integer variables (`NewIntVar`) or boolean variables (`NewBoolVar`). You cannot use standard Python `if/else` logic to evaluate the start time of a class, because the start time hasn't been decided yet! You must use OR-Tools mathematical equivalents like `AddModuloEquality` or `.OnlyEnforceIf()`.

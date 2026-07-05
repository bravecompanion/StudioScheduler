from ortools.sat.python import cp_model
from typing import List, Tuple
import re
from collections import defaultdict
from .models import ClassSession, Room, Teacher, StudioCalendar

class StudioSchedulerModel:
    def __init__(self, classes: List[ClassSession], rooms: List[Room], teachers: List[Teacher], cal: StudioCalendar):
        self.classes = classes
        self.rooms = rooms
        self.teachers = teachers
        self.cal = cal
        
        self.model = cp_model.CpModel()
        
        # Parse operating hours
        open_h, open_m = map(int, self.cal.open_time.split(':'))
        close_h, close_m = map(int, self.cal.close_time.split(':'))
        self.open_mins = open_h * 60 + open_m
        self.close_mins = close_h * 60 + close_m
        self.day_duration_epochs = (self.close_mins - self.open_mins) // self.cal.epoch_minutes
        
        # State variables
        self.class_vars = {}
        self.room_intervals = {r.id: [] for r in self.rooms}
        self.teacher_intervals = {t.id: [] for t in self.teachers}
        self.teacher_by_id = {t.id: t for t in self.teachers}
        
        self.penalties = []
        
    def create_variables(self):
        for c in self.classes:
            valid_start_epochs = []
            for d_idx, day_str in enumerate(self.cal.days):
                day_base = d_idx * self.cal.day_offset
                day_max_start = day_base + self.day_duration_epochs - c.duration_epochs
                if day_max_start >= day_base:
                    valid_start_epochs.extend(list(range(day_base, day_max_start + 1)))
                    
            if not valid_start_epochs:
                print(f"WARNING: Class {c.id} duration ({c.duration_epochs} epochs) is longer than the operating window!")
                continue
                
            domain = cp_model.Domain.FromValues(valid_start_epochs)
            start_var = self.model.NewIntVarFromDomain(domain, f'start_{c.id}')
            end_var = self.model.NewIntVar(0, self.cal.day_offset * len(self.cal.days), f'end_{c.id}')
            interval_var = self.model.NewIntervalVar(start_var, c.duration_epochs, end_var, f'interval_{c.id}')
            
            # Room variables
            room_presences = {}
            for r in self.rooms:
                if r.capacity >= c.size:
                    presence = self.model.NewBoolVar(f'room_presence_{c.id}_{r.id}')
                    opt_interval = self.model.NewOptionalIntervalVar(
                        start_var, c.duration_epochs, end_var, presence, f'room_opt_{c.id}_{r.id}'
                    )
                    self.room_intervals[r.id].append(opt_interval)
                    room_presences[r.id] = presence
                    
            # Teacher variables
            teacher_presences = {}
            valid_teachers = c.preferred_teachers if c.preferred_teachers else [t.id for t in self.teachers]
            
            if c.pinned_teacher and c.pinned_teacher not in valid_teachers:
                valid_teachers.append(c.pinned_teacher)
                
            for t_id in valid_teachers:
                if t_id not in self.teacher_by_id:
                    print(f"NOTICE: Teacher '{t_id}' is not in teachers.yaml. Assuming full availability.")
                    t = Teacher(id=t_id, avail_days=self.cal.days, avail_start_epoch=0, avail_end_epoch=self.day_duration_epochs)
                    self.teachers.append(t)
                    self.teacher_by_id[t_id] = t
                    self.teacher_intervals[t_id] = []
                    
                presence = self.model.NewBoolVar(f'teacher_presence_{c.id}_{t_id}')
                opt_interval = self.model.NewOptionalIntervalVar(
                    start_var, c.duration_epochs, end_var, presence, f'teacher_opt_{c.id}_{t_id}'
                )
                self.teacher_intervals[t_id].append(opt_interval)
                teacher_presences[t_id] = presence
                
            self.class_vars[c.id] = {
                'start': start_var,
                'end': end_var,
                'interval': interval_var,
                'room_presences': room_presences,
                'teacher_presences': teacher_presences
            }

    def add_hard_constraints(self):
        # Enforce exactly one room/teacher and pinning
        for c in self.classes:
            if c.id not in self.class_vars: continue
            c_vars = self.class_vars[c.id]
            
            # Room pinning and exactly one
            room_presences = c_vars['room_presences']
            if room_presences:
                self.model.AddExactlyOne(list(room_presences.values()))
                if c.pinned_room:
                    if c.pinned_room in room_presences:
                        self.model.Add(room_presences[c.pinned_room] == 1)
                    else:
                        print(f"CRITICAL ERROR: Class '{c.id}' is pinned to Room '{c.pinned_room}' but it's excluded (likely size).")
            
            # Teacher pinning and exactly one
            teacher_presences = c_vars['teacher_presences']
            if teacher_presences:
                self.model.AddExactlyOne(list(teacher_presences.values()))
                if c.pinned_teacher:
                    if c.pinned_teacher in teacher_presences:
                        self.model.Add(teacher_presences[c.pinned_teacher] == 1)
                    else:
                        print(f"CRITICAL ERROR: Class '{c.id}' is pinned to Teacher '{c.pinned_teacher}' but they were excluded.")
                        
            # Time pinning
            if c.pinned_time_epoch is not None:
                self.model.Add(c_vars['start'] == c.pinned_time_epoch)
                
            # Teacher Availability (Day of Week and Time)
            start_var = c_vars['start']
            epoch_in_day = self.model.NewIntVar(0, self.cal.day_offset - 1, f'epoch_in_day_hard_{c.id}')
            self.model.AddModuloEquality(epoch_in_day, start_var, self.cal.day_offset)
            
            day_idx_var = self.model.NewIntVar(0, len(self.cal.days) - 1, f'day_idx_hard_{c.id}')
            self.model.AddDivisionEquality(day_idx_var, start_var, self.cal.day_offset)
            
            for t_id, presence in teacher_presences.items():
                t = self.teacher_by_id[t_id]
                
                # Day of Week Availability
                if getattr(t, 'avail_days', None):
                    valid_day_indices = [i for i, d in enumerate(self.cal.days) if d in t.avail_days]
                    invalid_day_indices = [i for i in range(len(self.cal.days)) if i not in valid_day_indices]
                    
                    for inv_idx in invalid_day_indices:
                        is_invalid_day = self.model.NewBoolVar(f'inv_day_{c.id}_{t.id}_{inv_idx}')
                        self.model.Add(day_idx_var == inv_idx).OnlyEnforceIf(is_invalid_day)
                        self.model.Add(day_idx_var != inv_idx).OnlyEnforceIf(is_invalid_day.Not())
                        
                        # If the class falls on an invalid day, the teacher cannot be assigned to it.
                        self.model.AddImplication(is_invalid_day, presence.Not())
                        
                # Time of Day Availability
                if getattr(t, 'avail_start_epoch', None) is not None:
                    self.model.Add(epoch_in_day >= t.avail_start_epoch).OnlyEnforceIf(presence)
                    
                if getattr(t, 'avail_end_epoch', None) is not None:
                    self.model.Add(epoch_in_day + c.duration_epochs <= t.avail_end_epoch).OnlyEnforceIf(presence)
                
        # NoOverlap for Rooms
        for r_id, intervals in self.room_intervals.items():
            if intervals:
                self.model.AddNoOverlap(intervals)
                
        # NoOverlap for Teachers
        for t_id, intervals in self.teacher_intervals.items():
            if intervals:
                self.model.AddNoOverlap(intervals)

    def add_soft_constraints(self):
        self._penalize_late_young_classes()
        self._penalize_session_clustering()
        self._penalize_teacher_schedule_span()
        self._penalize_teacher_days_requested()
        
        if self.penalties:
            self.model.Minimize(sum(self.penalties))

    def _penalize_late_young_classes(self):
        """Soft Constraint: Push younger age groups to earlier time slots (Piecewise Step)."""
        # Target cutoff time: 17:30 (5:30 PM). This is 105 mins after open.
        target_mins_after_open = 120
        target_epoch = target_mins_after_open // self.cal.epoch_minutes
        
        for c in self.classes:
            if c.id not in self.class_vars: continue
            
            weight = max(0, 18 - c.age_min)
            if weight > 0:
                end_var = self.class_vars[c.id]['end']
                epoch_in_day = self.model.NewIntVar(0, self.cal.day_offset - 1, f'epoch_in_day_{c.id}')
                self.model.AddModuloEquality(epoch_in_day, end_var, self.cal.day_offset)
                
                # Piecewise Linear: 0 penalty before target, linearly scaling penalty after target
                late_epochs = self.model.NewIntVar(0, self.cal.day_offset, f'late_epochs_{c.id}')
                self.model.AddMaxEquality(late_epochs, [0, epoch_in_day - target_epoch])
                
                self.penalties.append(late_epochs * weight)

    def _penalize_session_clustering(self):
        """Soft Constraint: Diversify the days on which multiple sessions of the same class are offered."""
        CLUSTER_PENALTY = 75

        # Group classes by base name
        base_class_groups = defaultdict(list)
        for c in self.classes:
            if c.id not in self.class_vars: continue
            # Strip trailing _1, _2, etc. to get the base class name
            base_name = re.sub(r'_\d+$', '', c.id)
            base_class_groups[base_name].append(c)
            
        for base_name, class_list in base_class_groups.items():
            if len(class_list) <= 1:
                continue # No need to diversify a single session
                
            # Create a day variable for each session
            for c in class_list:
                start_var = self.class_vars[c.id]['start']
                day_var = self.model.NewIntVar(0, len(self.cal.days) - 1, f'day_idx_{c.id}')
                self.model.AddDivisionEquality(day_var, start_var, self.cal.day_offset)
                
            # For each day, count how many sessions land on it
            for d_idx, day_str in enumerate(self.cal.days):
                sessions_on_this_day = []
                for c in class_list:
                    start_var = self.class_vars[c.id]['start']
                    day_var = self.model.NewIntVar(0, len(self.cal.days) - 1, f'day_idx_{c.id}_{d_idx}')
                    self.model.AddDivisionEquality(day_var, start_var, self.cal.day_offset)
                    is_on_day = self.model.NewBoolVar(f'is_on_{day_str}_{c.id}')
                    self.model.Add(day_var == d_idx).OnlyEnforceIf(is_on_day)
                    self.model.Add(day_var != d_idx).OnlyEnforceIf(is_on_day.Not())
                    sessions_on_this_day.append(is_on_day)
                    
                count_on_day = self.model.NewIntVar(0, len(class_list), f'count_{base_name}_{day_str}')
                self.model.Add(count_on_day == sum(sessions_on_this_day))
                
                # Penalize the square of the count to heavily prioritize even distributions.
                # Example: 4 on Monday (16 penalty) vs 2 on Mon and 2 on Tue (4 + 4 = 8 penalty)
                count_sq = self.model.NewIntVar(0, len(class_list) ** 2, f'count_sq_{base_name}_{day_str}')
                self.model.AddMultiplicationEquality(count_sq, [count_on_day, count_on_day])
                
                # Multiply by a solid weight to heavily prioritize spreading them out
                self.penalties.append(count_sq * CLUSTER_PENALTY)

    def _penalize_teacher_schedule_span(self):
        """Soft Constraint: Minimize the daily span of classes for a teacher to compact their schedule."""
        for t in self.teachers:
            if not self.teacher_intervals[t.id]: continue
                
            for d_idx, day_str in enumerate(self.cal.days):
                day_base = d_idx * self.cal.day_offset
                day_end = day_base + self.day_duration_epochs
                
                starts = []
                ends = []
                
                for c in self.classes:
                    if c.id not in self.class_vars: continue
                    if t.id not in self.class_vars[c.id]['teacher_presences']: continue
                    
                    presence = self.class_vars[c.id]['teacher_presences'][t.id]
                    start_var = self.class_vars[c.id]['start']
                    end_var = self.class_vars[c.id]['end']
                    
                    day_var = self.model.NewIntVar(0, len(self.cal.days) - 1, f'span_day_idx_{c.id}_{t.id}_{d_idx}')
                    self.model.AddDivisionEquality(day_var, start_var, self.cal.day_offset)
                    
                    is_on_day = self.model.NewBoolVar(f'span_is_on_day_{d_idx}_{c.id}_{t.id}')
                    self.model.Add(day_var == d_idx).OnlyEnforceIf(is_on_day)
                    self.model.Add(day_var != d_idx).OnlyEnforceIf(is_on_day.Not())
                    
                    is_active_class = self.model.NewBoolVar(f'span_is_active_{c.id}_{t.id}_{d_idx}')
                    self.model.AddBoolAnd([presence, is_on_day]).OnlyEnforceIf(is_active_class)
                    self.model.AddBoolOr([presence.Not(), is_on_day.Not()]).OnlyEnforceIf(is_active_class.Not())
                    
                    safe_start = self.model.NewIntVar(0, self.cal.day_offset * len(self.cal.days), f'safe_start_{c.id}_{t.id}_{d_idx}')
                    self.model.Add(safe_start == start_var).OnlyEnforceIf(is_active_class)
                    self.model.Add(safe_start == day_end).OnlyEnforceIf(is_active_class.Not())
                    
                    safe_end = self.model.NewIntVar(0, self.cal.day_offset * len(self.cal.days), f'safe_end_{c.id}_{t.id}_{d_idx}')
                    self.model.Add(safe_end == end_var).OnlyEnforceIf(is_active_class)
                    self.model.Add(safe_end == day_base).OnlyEnforceIf(is_active_class.Not())
                    
                    starts.append(safe_start)
                    ends.append(safe_end)
                    
                if not starts: continue
                    
                min_start = self.model.NewIntVar(0, self.cal.day_offset * len(self.cal.days), f'min_start_{t.id}_{d_idx}')
                max_end = self.model.NewIntVar(0, self.cal.day_offset * len(self.cal.days), f'max_end_{t.id}_{d_idx}')
                self.model.AddMinEquality(min_start, starts)
                self.model.AddMaxEquality(max_end, ends)
                
                span = self.model.NewIntVar(0, self.day_duration_epochs, f'span_{t.id}_{d_idx}')
                self.model.AddMaxEquality(span, [0, max_end - min_start])
                self.penalties.append(span * 10)

    def _penalize_teacher_days_requested(self):
        """Soft Constraint: Penalize if a teacher is active on more or fewer days than they requested."""
        for t in self.teachers:
            if not getattr(t, 'days_requested', None): continue
                
            active_days = []
            for d_idx, day_str in enumerate(self.cal.days):
                classes_on_day = []
                for c in self.classes:
                    if c.id not in self.class_vars: continue
                    if t.id not in self.class_vars[c.id]['teacher_presences']: continue
                    
                    presence = self.class_vars[c.id]['teacher_presences'][t.id]
                    start_var = self.class_vars[c.id]['start']
                    
                    day_var = self.model.NewIntVar(0, len(self.cal.days) - 1, f'req_day_idx_{c.id}_{t.id}_{d_idx}')
                    self.model.AddDivisionEquality(day_var, start_var, self.cal.day_offset)
                    
                    is_on_day = self.model.NewBoolVar(f'req_is_on_day_{d_idx}_{c.id}_{t.id}')
                    self.model.Add(day_var == d_idx).OnlyEnforceIf(is_on_day)
                    self.model.Add(day_var != d_idx).OnlyEnforceIf(is_on_day.Not())
                    
                    is_active_class = self.model.NewBoolVar(f'req_is_active_{c.id}_{t.id}_{d_idx}')
                    self.model.AddBoolAnd([presence, is_on_day]).OnlyEnforceIf(is_active_class)
                    self.model.AddBoolOr([presence.Not(), is_on_day.Not()]).OnlyEnforceIf(is_active_class.Not())
                    
                    classes_on_day.append(is_active_class)
                    
                is_active_on_day = self.model.NewBoolVar(f'active_on_day_{t.id}_{d_idx}')
                if classes_on_day:
                    self.model.AddMaxEquality(is_active_on_day, classes_on_day)
                else:
                    self.model.Add(is_active_on_day == 0)
                    
                active_days.append(is_active_on_day)
                
            total_active = self.model.NewIntVar(0, len(self.cal.days), f'total_active_days_{t.id}')
            self.model.Add(total_active == sum(active_days))
            
            diff1 = total_active - t.days_requested
            diff2 = t.days_requested - total_active
            abs_diff = self.model.NewIntVar(0, len(self.cal.days), f'abs_diff_{t.id}')
            self.model.AddMaxEquality(abs_diff, [diff1, diff2])
            
            self.penalties.append(abs_diff * 10000)

    def validate_inputs(self):
        """Sanity checker that runs before the CP-SAT engine to find impossible constraints."""
        import sys
        errors = []
        
        def to_time(epoch_in_day):
            open_h, open_m = map(int, self.cal.open_time.split(':'))
            minutes_since_open = epoch_in_day * self.cal.epoch_minutes
            total_minutes = open_h * 60 + open_m + minutes_since_open
            h = total_minutes // 60
            m = total_minutes % 60
            period = "PM" if h >= 12 else "AM"
            h_12 = h - 12 if h > 12 else h
            h_12 = 12 if h_12 == 0 else h_12
            return f"{h_12}:{m:02d} {period}"
            
        teacher_pinned_times = {}
        room_pinned_times = {}
        
        for c in self.classes:
            valid_t = [t for t in self.teachers if t.id in (c.preferred_teachers or []) or t.id == c.pinned_teacher]
            if not valid_t:
                errors.append(f"Class '{c.id}' has no valid teachers assigned (not in preferred list, and no pinned teacher).")
                
            if c.pinned_teacher and c.pinned_time_epoch is not None:
                t = self.teacher_by_id.get(c.pinned_teacher)
                if not t:
                    errors.append(f"Class '{c.id}' is pinned to unknown teacher '{c.pinned_teacher}'.")
                    continue
                    
                day_idx = c.pinned_time_epoch // self.cal.day_offset
                day_str = self.cal.days[day_idx]
                epoch_in_day = c.pinned_time_epoch % self.cal.day_offset
                
                if getattr(t, 'avail_days', None) and day_str not in t.avail_days:
                    errors.append(f"Class '{c.id}' is pinned to {day_str}, but Teacher '{t.id}' does not work on {day_str}. (Available: {', '.join(t.avail_days)})")
                    
                if getattr(t, 'avail_start_epoch', None) is not None and epoch_in_day < t.avail_start_epoch:
                    errors.append(f"Class '{c.id}' is pinned to start at {to_time(epoch_in_day)}, but Teacher '{t.id}' does not start until {to_time(t.avail_start_epoch)}.")
                    
                if getattr(t, 'avail_end_epoch', None) is not None and (epoch_in_day + c.duration_epochs) > t.avail_end_epoch:
                    errors.append(f"Class '{c.id}' ends at {to_time(epoch_in_day + c.duration_epochs)}, but Teacher '{t.id}' leaves at {to_time(t.avail_end_epoch)}.")
                    
                c_start = c.pinned_time_epoch
                c_end = c.pinned_time_epoch + c.duration_epochs
                if t.id not in teacher_pinned_times:
                    teacher_pinned_times[t.id] = []
                    
                for other_start, other_end, other_c in teacher_pinned_times[t.id]:
                    if max(c_start, other_start) < min(c_end, other_end):
                        errors.append(f"Teacher '{t.id}' is double-booked! Class '{c.id}' overlaps with Class '{other_c}'.")
                        
                teacher_pinned_times[t.id].append((c_start, c_end, c.id))
                
            if getattr(c, 'pinned_room', None) and c.pinned_time_epoch is not None:
                r_id = str(c.pinned_room).strip()
                r = next((rm for rm in self.rooms if rm.id == r_id), None)
                if not r:
                    errors.append(f"Class '{c.id}' is pinned to unknown room '{r_id}'.")
                else:
                    if getattr(c, 'size', 0) > r.capacity:
                        errors.append(f"Class '{c.id}' (size {c.size}) is pinned to Room '{r_id}', which only holds {r.capacity}.")
                    
                    c_start = c.pinned_time_epoch
                    c_end = c.pinned_time_epoch + c.duration_epochs
                    if r_id not in room_pinned_times:
                        room_pinned_times[r_id] = []
                        
                    for other_start, other_end, other_c in room_pinned_times[r_id]:
                        if max(c_start, other_start) < min(c_end, other_end):
                            errors.append(f"Room '{r_id}' is double-booked! Class '{c.id}' overlaps with Class '{other_c}'.")
                            
                    room_pinned_times[r_id].append((c_start, c_end, c.id))
                
        if errors:
            print("\n" + "="*80)
            print("🚨 PRE-SOLVER VALIDATION FAILED! 🚨")
            print("The following hard constraints are physically impossible:\n")
            for e in errors:
                print(f"❌ {e}")
            print("="*80 + "\n")
            print("Please fix these errors in classes.csv or teachers.yaml and try again.")
            sys.exit(1)

    def solve(self):
        self.validate_inputs()
        
        solver = cp_model.CpSolver()
        solver.parameters.log_search_progress = True
        solver.parameters.max_time_in_seconds = 60.0
        solver.parameters.num_search_workers = 24
        
        print("Starting solver...")
        status = solver.Solve(self.model)
        
        results = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"Solved successfully! Status: {solver.StatusName(status)}")
            for c in self.classes:
                if c.id not in self.class_vars: continue
                c_vars = self.class_vars[c.id]
                start_val = solver.Value(c_vars['start'])
                
                assigned_room = "Unknown"
                for r_id, p_var in c_vars['room_presences'].items():
                    if solver.Value(p_var):
                        assigned_room = r_id
                        break
                        
                assigned_teacher = "Unknown"
                for t_id, p_var in c_vars['teacher_presences'].items():
                    if solver.Value(p_var):
                        assigned_teacher = t_id
                        break
                        
                # Convert global epoch back to day and time
                day_idx = start_val // self.cal.day_offset
                day_str = self.cal.days[day_idx]
                
                epoch_in_day = start_val % self.cal.day_offset
                mins_since_open = epoch_in_day * self.cal.epoch_minutes
                mins_since_midnight = self.open_mins + mins_since_open
                
                hour = mins_since_midnight // 60
                minute = mins_since_midnight % 60
                time_str = f"{hour:02d}:{minute:02d}"
                
                results.append({
                    'Class_ID': c.id,
                    'Style': c.style,
                    'Cohort': c.cohort,
                    'Day': day_str,
                    'Start_Time': time_str,
                    'Duration_Mins': c.duration_epochs * self.cal.epoch_minutes,
                    'Room': assigned_room,
                    'Teacher': assigned_teacher,
                    'Is_Pinned_Teacher': c.pinned_teacher is not None,
                    'Is_Pinned_Time': c.pinned_time_epoch is not None,
                    'Is_Pinned_Room': c.pinned_room is not None
                })
                
            return results, status
        else:
            print(f"Solver failed. Status: {solver.StatusName(status)}")
            return None, status

def build_and_solve(classes: List[ClassSession], rooms: List[Room], teachers: List[Teacher], cal: StudioCalendar):
    scheduler = StudioSchedulerModel(classes, rooms, teachers, cal)
    scheduler.create_variables()
    scheduler.add_hard_constraints()
    scheduler.add_soft_constraints()
    return scheduler.solve()

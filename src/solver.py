from ortools.sat.python import cp_model
from typing import List, Tuple
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
        
        if self.penalties:
            self.model.Minimize(sum(self.penalties))

    def _penalize_late_young_classes(self):
        """Soft Constraint: Push younger age groups to earlier time slots (Piecewise Step)."""
        # Target cutoff time: 17:00 (5:00 PM). This is 90 mins after open (15:30).
        target_mins_after_open = 90
        target_epoch = target_mins_after_open // self.cal.epoch_minutes
        
        for c in self.classes:
            if c.id not in self.class_vars: continue
            
            weight = max(0, 18 - c.age_min)
            if weight > 0:
                start_var = self.class_vars[c.id]['start']
                epoch_in_day = self.model.NewIntVar(0, self.cal.day_offset - 1, f'epoch_in_day_{c.id}')
                self.model.AddModuloEquality(epoch_in_day, start_var, self.cal.day_offset)
                
                # Piecewise Linear: 0 penalty before target, linearly scaling penalty after target
                late_epochs = self.model.NewIntVar(0, self.cal.day_offset, f'late_epochs_{c.id}')
                self.model.AddMaxEquality(late_epochs, [0, epoch_in_day - target_epoch])
                
                self.penalties.append(late_epochs * weight)

    def solve(self):
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

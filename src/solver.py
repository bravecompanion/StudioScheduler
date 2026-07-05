from ortools.sat.python import cp_model
from typing import List, Tuple
from .models import ClassSession, Room, Teacher, StudioCalendar

def build_and_solve(classes: List[ClassSession], rooms: List[Room], teachers: List[Teacher], cal: StudioCalendar):
    model = cp_model.CpModel()
    
    open_h, open_m = map(int, cal.open_time.split(':'))
    close_h, close_m = map(int, cal.close_time.split(':'))
    open_mins = open_h * 60 + open_m
    close_mins = close_h * 60 + close_m
    day_duration_epochs = (close_mins - open_mins) // cal.epoch_minutes
    
    class_vars = {}
    room_intervals = {r.id: [] for r in rooms}
    teacher_intervals = {t.id: [] for t in teachers}
    teacher_by_id = {t.id: t for t in teachers}
    
    for c in classes:
        valid_start_epochs = []
        for d_idx, day_str in enumerate(cal.days):
            day_base = d_idx * cal.day_offset
            day_max_start = day_base + day_duration_epochs - c.duration_epochs
            if day_max_start >= day_base:
                valid_start_epochs.extend(list(range(day_base, day_max_start + 1)))
                
        if not valid_start_epochs:
            print(f"WARNING: Class {c.id} duration ({c.duration_epochs} epochs) is longer than the operating window!")
            continue
            
        domain = cp_model.Domain.FromValues(valid_start_epochs)
        start_var = model.NewIntVarFromDomain(domain, f'start_{c.id}')
        end_var = model.NewIntVar(0, cal.day_offset * len(cal.days), f'end_{c.id}')
        interval_var = model.NewIntervalVar(start_var, c.duration_epochs, end_var, f'interval_{c.id}')
        
        room_presences = {}
        for r in rooms:
            if r.capacity >= c.size:
                presence = model.NewBoolVar(f'room_presence_{c.id}_{r.id}')
                opt_interval = model.NewOptionalIntervalVar(
                    start_var, c.duration_epochs, end_var, presence, f'room_opt_{c.id}_{r.id}'
                )
                room_intervals[r.id].append(opt_interval)
                room_presences[r.id] = presence
                
                if c.pinned_room and c.pinned_room != r.id:
                    model.Add(presence == 0)
                elif c.pinned_room and c.pinned_room == r.id:
                    model.Add(presence == 1)
                    
        if room_presences:
            model.AddExactlyOne(list(room_presences.values()))
            
            # Sanity check: if we pinned a room, make sure it's actually in the generated presences!
            if c.pinned_room and c.pinned_room not in room_presences:
                print(f"CRITICAL ERROR: Class '{c.id}' is pinned to Room '{c.pinned_room}', but that room was excluded (likely because room capacity < class size). This will cause INFEASIBLE.")
        else:
            print(f"WARNING: No rooms big enough for {c.id} (size {c.size})")
            
        teacher_presences = {}
        valid_teachers = c.preferred_teachers if c.preferred_teachers else [t.id for t in teachers]
        
        # If pinned to a teacher, ensure they are considered valid even if not in the YAML options
        if c.pinned_teacher and c.pinned_teacher not in valid_teachers:
            print(f"WARNING: Class '{c.id}' is pinned to '{c.pinned_teacher}', but they aren't in the preferred teachers list. Adding them to valid teachers.")
            valid_teachers.append(c.pinned_teacher)
            
        for t_id in valid_teachers:
            if t_id not in teacher_by_id:
                print(f"NOTICE: Teacher '{t_id}' is not in teachers.yaml. Assuming full availability.")
                t = Teacher(id=t_id, avail_days=cal.days, avail_start_epoch=0, avail_end_epoch=day_duration_epochs)
                teachers.append(t)
                teacher_by_id[t_id] = t
                teacher_intervals[t_id] = []
                
            presence = model.NewBoolVar(f'teacher_presence_{c.id}_{t_id}')
            opt_interval = model.NewOptionalIntervalVar(
                start_var, c.duration_epochs, end_var, presence, f'teacher_opt_{c.id}_{t_id}'
            )
            teacher_intervals[t_id].append(opt_interval)
            teacher_presences[t_id] = presence
            
            if c.pinned_teacher and c.pinned_teacher != t_id:
                model.Add(presence == 0)
            elif c.pinned_teacher and c.pinned_teacher == t_id:
                model.Add(presence == 1)
                
        if teacher_presences:
            model.AddExactlyOne(list(teacher_presences.values()))
            
            if c.pinned_teacher and c.pinned_teacher not in teacher_presences:
                print(f"CRITICAL ERROR: Class '{c.id}' is pinned to Teacher '{c.pinned_teacher}', but they were excluded. This will cause INFEASIBLE.")
        else:
            print(f"WARNING: No valid teachers for {c.id}")
            
        if c.pinned_time_epoch is not None:
            model.Add(start_var == c.pinned_time_epoch)
            
        class_vars[c.id] = {
            'start': start_var,
            'room_presences': room_presences,
            'teacher_presences': teacher_presences
        }
        
    for r_id, intervals in room_intervals.items():
        if intervals:
            model.AddNoOverlap(intervals)
            
    for t_id, intervals in teacher_intervals.items():
        if intervals:
            model.AddNoOverlap(intervals)
            
    # === OBJECTIVE FUNCTION ===
    penalties = []
    
    # 1. Soft Constraint: Push younger age groups earlier
    for c in classes:
        if c.id not in class_vars: continue
        c_vars = class_vars[c.id]
        start_var = c_vars['start']
        
        # Weight = (20 - age_min). If age_min is 5, weight is 15. If age_min is 20, weight is 0.
        weight = max(0, 20 - c.age_min)
        if weight > 0:
            # Optimize for the time OF THE DAY, not the absolute time in the week.
            epoch_in_day = model.NewIntVar(0, cal.day_offset - 1, f'epoch_in_day_{c.id}')
            model.AddModuloEquality(epoch_in_day, start_var, cal.day_offset)
            penalties.append(epoch_in_day * weight)
            
    if penalties:
        model.Minimize(sum(penalties))
        
    solver = cp_model.CpSolver()
    solver.parameters.log_search_progress = True
    solver.parameters.max_time_in_seconds = 60.0
    solver.parameters.num_search_workers = 24  # Utilizing i7-14700 (24 threads)
    
    print("Starting solver...")
    status = solver.Solve(model)
    
    results = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Solved successfully! Status: {solver.StatusName(status)}")
        for c in classes:
            if c.id not in class_vars: continue
            c_vars = class_vars[c.id]
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
            day_idx = start_val // cal.day_offset
            day_str = cal.days[day_idx]
            
            epoch_in_day = start_val % cal.day_offset
            mins_since_open = epoch_in_day * cal.epoch_minutes
            mins_since_midnight = open_mins + mins_since_open
            
            hour = mins_since_midnight // 60
            minute = mins_since_midnight % 60
            time_str = f"{hour:02d}:{minute:02d}"
            
            results.append({
                'Class_ID': c.id,
                'Style': c.style,
                'Cohort': c.cohort,
                'Day': day_str,
                'Start_Time': time_str,
                'Duration_Mins': c.duration_epochs * cal.epoch_minutes,
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

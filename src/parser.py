import pandas as pd
import yaml
import re
from typing import List, Dict, Tuple
from .models import Room, Teacher, ClassSession, StudioCalendar

def time_to_epochs(time_str, cal: StudioCalendar) -> int:
    if time_str is None or str(time_str).strip() == '':
        return None
        
    open_h, open_m = map(int, cal.open_time.split(':'))
    open_minutes_since_midnight = open_h * 60 + open_m
    
    if isinstance(time_str, int):
        # PyYAML 1.1 automatically parses unquoted HH:MM as sexagesimal (minutes since midnight)
        minutes_since_midnight = time_str
    else:
        time_str = str(time_str).strip().upper()
        if ":" not in time_str:
            return None
            
        is_pm = 'PM' in time_str
        is_am = 'AM' in time_str
        
        # Remove AM/PM and trailing spaces
        time_str = time_str.replace('PM', '').replace('AM', '').strip()
        
        parts = time_str.split(':')
        h = int(parts[0])
        m = int(parts[1])
        
        if is_pm and h < 12:
            h += 12
        if is_am and h == 12:
            h = 0
            
        minutes_since_midnight = h * 60 + m
        
    minutes_since_open = minutes_since_midnight - open_minutes_since_midnight
    return minutes_since_open // cal.epoch_minutes

def parse_pinned_time(pin_str: str, cal: StudioCalendar) -> int:
    """Converts a pinned string like 'Monday-16:00' to a global epoch."""
    if pd.isna(pin_str) or not str(pin_str).strip():
        return None
    pin_str = str(pin_str).strip()
    parts = pin_str.split('-')
    if len(parts) != 2:
        return None
    
    day_str, time_str = parts
    day_str = day_str.upper()[:3] # MON, TUE, etc.
    
    if day_str not in cal.days:
        return None
        
    day_idx = cal.days.index(day_str)
    day_base = day_idx * cal.day_offset
    
    epochs = time_to_epochs(time_str, cal)
    if epochs is None:
        return None
        
    return day_base + epochs

def load_data(
    rooms_csv: str, 
    teachers_yaml: str, 
    teacher_options_yaml: str, 
    classes_csv: str,
    cal: StudioCalendar
) -> Tuple[List[Room], List[Teacher], List[ClassSession]]:
    
    # 1. Load Rooms
    rooms_df = pd.read_csv(rooms_csv)
    rooms = []
    for _, row in rooms_df.iterrows():
        rooms.append(Room(id=str(row['Room_ID']), capacity=int(row['Capacity'])))
        
    # 2. Load Teachers
    with open(teachers_yaml, 'r') as f:
        teachers_raw = yaml.safe_load(f)
        
    teachers = []
    for t_id, t_data in teachers_raw.items():
        if not isinstance(t_data, dict):
            continue
            
        availability = {}
        avail_raw = t_data.get('availability', {})
        if isinstance(avail_raw, dict):
            for day, times in avail_raw.items():
                if isinstance(times, dict):
                    day_str = str(day).upper()
                    start_epoch = time_to_epochs(times.get('start', ''), cal)
                    end_epoch = time_to_epochs(times.get('end', ''), cal)
                    availability[day_str] = {'start': start_epoch, 'end': end_epoch}
        
        hate_classes = [str(s).lower() for s in t_data.get('hate_class', []) or []]
        hate_cohorts = [str(c).strip().replace('"', '') for c in t_data.get('hate_cohort', []) or []]
        
        teachers.append(Teacher(
            id=t_id,
            availability=availability,
            days_requested=t_data.get('days_requested'),
            hate_classes=hate_classes,
            hate_cohorts=hate_cohorts
        ))
        
    # 3. Load Teacher Options (Routing table)
    with open(teacher_options_yaml, 'r') as f:
        options_raw = yaml.safe_load(f)
        
    # Build routing dict: (style, cohort) -> [teachers]
    routing_table = {}
    for block in options_raw:
        if not block:
            continue
        types = block.get('type', [])
        if isinstance(types, str):
            types = [types]
        if not types:
            continue
            
        cohort = str(block.get('cohort', '')).strip()
        pref_teachers = block.get('teachers', [])
        
        for t in types:
            routing_table[(t.lower(), cohort)] = pref_teachers
            
    # 4. Load Classes
    classes_df = pd.read_csv(classes_csv)
    classes = []
    for _, row in classes_df.iterrows():
        if pd.isna(row['ClassName']):
            continue
        class_id = str(row['ClassName']).strip()
        if not class_id or class_id.lower() == 'nan':
            continue
            
        style = str(row['Style']).lower()
        cohort = str(row['Cohort']).strip() if not pd.isna(row['Cohort']) else "all"
        # CSV sometimes has escaped quotes like """12+""", strip them
        cohort = cohort.replace('"', '').strip()
        
        # Find preferred teachers. Match exact style + cohort.
        pref_teachers = routing_table.get((style, cohort), [])
        if not pref_teachers:
             pref_teachers = routing_table.get((style, "all"), [])
        
        if pd.isna(row['Duration']):
            print(f"WARNING: Class {class_id} is missing a Duration. Defaulting to 45 mins.")
            duration_mins = 45
        else:
            duration_mins = int(row['Duration'])
        duration_epochs = duration_mins // cal.epoch_minutes
        
        size = int(row['Size']) if not pd.isna(row['Size']) else 20
        
        pin_t = row['PinTeacher']
        if pd.isna(pin_t):
            pin_t = None
        else:
            pin_t = str(pin_t).strip()
            
        pin_r = row['PinRoom']
        if pd.isna(pin_r):
            pin_r = None
        else:
            if isinstance(pin_r, float) and pin_r.is_integer():
                pin_r = str(int(pin_r))
            else:
                pin_r = str(pin_r).strip()
        
        # Handle the user's "either-or" co-teaching string: 'EmilyJ/Cameron'
        if pin_t and '/' in pin_t:
            pref_teachers = pin_t.split('/')
            pin_t = None
            
        pin_time = parse_pinned_time(row['PinTime'], cal)
        
        # Parse Age
        age_min = int(row['AgeMin']) if 'AgeMin' in row and not pd.isna(row['AgeMin']) else 20
        age_max = int(row['AgeMax']) if 'AgeMax' in row and not pd.isna(row['AgeMax']) else 20
        
        # Parse Time Windows
        allowed_days = None
        if 'AllowedDays' in row and not pd.isna(row['AllowedDays']):
            allowed_days = [d.strip().upper() for d in str(row['AllowedDays']).split(',')]
            
        earliest_start_epoch = None
        if 'EarliestStart' in row and not pd.isna(row['EarliestStart']):
            earliest_start_epoch = time_to_epochs(row['EarliestStart'], cal)
            
        latest_end_epoch = None
        if 'LatestEnd' in row and not pd.isna(row['LatestEnd']):
            latest_end_epoch = time_to_epochs(row['LatestEnd'], cal)
        
        classes.append(ClassSession(
            id=class_id,
            style=style,
            cohort=cohort,
            duration_epochs=duration_epochs,
            size=size,
            preferred_teachers=pref_teachers,
            pinned_teacher=pin_t,
            pinned_time_epoch=pin_time,
            pinned_room=pin_r,
            age_min=age_min,
            age_max=age_max,
            allowed_days=allowed_days,
            earliest_start_epoch=earliest_start_epoch,
            latest_end_epoch=latest_end_epoch
        ))
        
    return rooms, teachers, classes

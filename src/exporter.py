import os

def create_ical_content(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Dance Studio Scheduler//EN"
    ]
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"SUMMARY:{ev['summary']}")
        lines.append(f"DTSTART:{ev['start']}")
        lines.append(f"DTEND:{ev['end']}")
        lines.append(f"DESCRIPTION:{ev['description']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines)

def export_to_ical(results, output_dir="calendars"):
    os.makedirs(output_dir, exist_ok=True)
    
    # Map days to a generic reference week (e.g. August 2026 where Mon is the 3rd)
    # This allows standard calendar apps to display them properly in a week view
    day_map = {
        "MON": "20260803",
        "TUE": "20260804",
        "WED": "20260805",
        "THU": "20260806",
        "FRI": "20260807",
        "SAT": "20260808",
        "SUN": "20260809"
    }
    
    teacher_events = {}
    room_events = {}
    cohort_events = {}
    
    for r in results:
        day_str = r['Day']
        start_time = r['Start_Time'] # e.g. "15:30"
        dur_mins = r['Duration_Mins']
        
        start_h, start_m = map(int, start_time.split(':'))
        
        # Calculate end time
        end_mins_total = start_h * 60 + start_m + dur_mins
        end_h = end_mins_total // 60
        end_m = end_mins_total % 60
        
        date_str = day_map.get(day_str, "20260803")
        
        # iCal format: YYYYMMDDThhmmss
        dtstart = f"{date_str}T{start_h:02d}{start_m:02d}00"
        dtend = f"{date_str}T{end_h:02d}{end_m:02d}00"
        
        teacher = r['Teacher']
        room = r['Room']
        cohort = r['Cohort']
        class_id = r['Class_ID']
        
        desc = f"Teacher: {teacher}\\nRoom: {room}\\nCohort: {cohort}\\nStyle: {r.get('Style', '')}"
        
        ev_teacher = {
            "summary": f"{class_id} (Room {room})",
            "start": dtstart,
            "end": dtend,
            "description": desc
        }
        teacher_events.setdefault(teacher, []).append(ev_teacher)
        
        ev_room = {
            "summary": f"{class_id} ({teacher})",
            "start": dtstart,
            "end": dtend,
            "description": desc
        }
        room_events.setdefault(room, []).append(ev_room)
        
        ev_cohort = {
            "summary": f"{class_id} (Room {room}, {teacher})",
            "start": dtstart,
            "end": dtend,
            "description": desc
        }
        cohort_events.setdefault(cohort, []).append(ev_cohort)
        
    # Write files
    for t, evs in teacher_events.items():
        with open(os.path.join(output_dir, f"teacher_{t}.ics"), "w") as f:
            f.write(create_ical_content(evs))
            
    for r, evs in room_events.items():
        with open(os.path.join(output_dir, f"room_{r}.ics"), "w") as f:
            f.write(create_ical_content(evs))
            
    for c, evs in cohort_events.items():
        # sanitize cohort name for filename
        c_safe = c.replace("+", "plus").replace("-", "_").replace(" ", "")
        with open(os.path.join(output_dir, f"cohort_{c_safe}.ics"), "w") as f:
            f.write(create_ical_content(evs))
            
    print(f"Generated {len(teacher_events)} teacher calendars, {len(room_events)} room calendars, and {len(cohort_events)} cohort calendars in the '{output_dir}/' directory.")

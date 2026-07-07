import pandas as pd
from src.models import StudioCalendar
from src.parser import load_data
from src.solver import build_and_solve
from src.exporter import export_to_ical, export_to_json
from ortools.sat.python import cp_model

def main():
    print("Loading data...")
    cal = StudioCalendar(
        days=["MON", "TUE", "WED", "THU"],
        open_time="15:30",
        close_time="21:00",
        epoch_minutes=5,
        day_offset=1000
    )
    
    rooms, teachers, classes = load_data(
        rooms_csv="data/rooms.csv",
        teachers_yaml="teachers.yaml",
        teacher_options_yaml="teacher_options.yaml",
        classes_csv="classes.csv",
        cal=cal
    )
    
    print(f"Loaded {len(rooms)} rooms, {len(teachers)} teachers, {len(classes)} classes.")
    
    results, status = build_and_solve(classes, rooms, teachers, cal)
    
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values(by=['Day', 'Start_Time', 'Room'])
        df.to_csv("output_schedule.csv", index=False)
        print("Schedule saved to output_schedule.csv")
        
        # Generate iCal files
        export_to_ical(results)
        
        # Generate JSON file for frontend display
        export_to_json(results, cal=cal, all_teachers=teachers)
    else:
        print("No schedule could be generated.")

if __name__ == "__main__":
    main()

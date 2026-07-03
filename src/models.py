from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict

@dataclass
class Room:
    id: str
    capacity: int

@dataclass
class Teacher:
    id: str
    avail_days: List[str]
    avail_start_epoch: Optional[int]
    avail_end_epoch: Optional[int]

@dataclass
class ClassSession:
    id: str
    style: str
    cohort: str
    duration_epochs: int
    size: int
    preferred_teachers: List[str] = field(default_factory=list)
    pinned_teacher: Optional[str] = None
    pinned_time_epoch: Optional[int] = None
    pinned_room: Optional[str] = None

@dataclass
class StudioCalendar:
    days: List[str] = field(default_factory=lambda: ["MON", "TUE", "WED", "THU"])
    open_time: str = "15:30"
    close_time: str = "21:00"
    epoch_minutes: int = 5
    day_offset: int = 1000  # MON starts at 0, TUE at 1000, etc.


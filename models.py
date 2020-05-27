from enum import IntEnum, Enum
from typing import TypedDict, List, Dict
from datetime import datetime, timedelta


class EmployeeShiftColor(IntEnum):
    BLUE = 0
    GREEN = 1
    RED = 2
    ORANGE = 3
    PINK = 4


class ShiftState(str, Enum):
    Complete = 'complete'
    Incomplete = 'incomplete'


class ShiftComponentType(str, Enum):
    Actual = 'actual'
    Projected = 'projected'


class ShiftComponent(TypedDict):
    start: datetime
    end: datetime
    duration: timedelta
    state: ShiftState


class Shift(TypedDict):
    start: datetime
    end: datetime
    duration: int
    employeeId: str
    components: List[ShiftComponent]


class Employee(TypedDict):
    id: str
    name: str
    color: EmployeeShiftColor


class ShiftsResponse(TypedDict):
    shifts: List[Shift]
    employees: Dict[str, Employee]
    employeeIds: List[str]


class EmployeeShiftsResponse(TypedDict):
    employee: Employee
    shifts: List[Shift]


class GraphDataItem(TypedDict):
    _id: str
    buckets: Dict[str, int]
    total: int


class GraphDataResponse(TypedDict):
    employees: List[Employee]
    data: List[GraphDataItem]
    columns: List[str]

"""
Query Translator for Course Scheduling What-If Analysis

Based on X-MILP paper (Section 4.1): Translates natural language queries
into formal constraint expressions for counterfactual analysis.
"""

from typing import Dict, Any, List, Optional
from enum import Enum
import re


class QueryType(str, Enum):
    """Types of what-if queries users can ask"""
    # Enforce constraints (make something happen that didn't)
    ENFORCE_TIME_SLOT = "enforce_time_slot"
    ENFORCE_DAY = "enforce_day"
    ENFORCE_ROOM = "enforce_room"
    ENFORCE_BEFORE_TIME = "enforce_before_time"
    ENFORCE_AFTER_TIME = "enforce_after_time"
    ENFORCE_NO_LUNCH = "enforce_no_lunch"
    ENFORCE_CONSECUTIVE = "enforce_consecutive"
    
    # Veto constraints (prevent something that did happen)
    VETO_TIME_SLOT = "veto_time_slot"
    VETO_DAY = "veto_day"
    VETO_ROOM = "veto_room"
    VETO_LUNCH = "veto_lunch"
    VETO_INSTRUCTOR_DAY = "veto_instructor_day"
    
    # Combined constraints
    SWAP_TIME_SLOTS = "swap_time_slots"
    SWAP_ROOMS = "swap_rooms"


class QueryConstraint:
    """
    Represents a single query constraint
    Follows X-MILP paper Definition 4: Extended Problem M' = <f, C ∪ CQ>
    """
    
    def __init__(
        self,
        query_type: QueryType,
        course_id: Optional[str] = None,
        instructor_id: Optional[str] = None,
        week: Optional[int] = None,
        day: Optional[str] = None,
        period_start: Optional[int] = None,
        period_end: Optional[int] = None,
        room_id: Optional[str] = None,
        course_id_2: Optional[str] = None,  # For swap queries
        **kwargs
    ):
        self.query_type = query_type
        self.course_id = course_id
        self.instructor_id = instructor_id
        self.week = week
        self.day = day
        self.period_start = period_start
        self.period_end = period_end
        self.room_id = room_id
        self.course_id_2 = course_id_2
        self.extra_params = kwargs
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict for Julia solver"""
        return {
            "type": self.query_type.value,
            "course_id": self.course_id,
            "instructor_id": self.instructor_id,
            "week": self.week,
            "day": self.day,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "room_id": self.room_id,
            "course_id_2": self.course_id_2,
            **self.extra_params
        }
    
    def to_natural_language(self) -> str:
        """Convert back to human-readable description"""
        if self.query_type == QueryType.ENFORCE_TIME_SLOT:
            return f"Schedule {self.course_id} on {self.day} at period {self.period_start}"
        elif self.query_type == QueryType.VETO_DAY:
            target = self.course_id or f"instructor {self.instructor_id}"
            return f"Avoid scheduling {target} on {self.day}"
        elif self.query_type == QueryType.ENFORCE_NO_LUNCH:
            return f"Prevent {self.course_id} from being scheduled during lunch"
        elif self.query_type == QueryType.SWAP_TIME_SLOTS:
            return f"Swap {self.course_id} and {self.course_id_2} time slots"
        else:
            return f"Query type: {self.query_type}"


class QueryTranslator:
    """
    Translates user questions into formal query constraints
    
    Follows X-MILP paper Table 2: Set of possible user questions and encodings
    """
    
    def __init__(self):
        self.query_patterns = self._build_query_patterns()
    
    def parse_structured_query(
        self,
        query_type: str,
        params: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> List[QueryConstraint]:
        """
        Parse a structured query from the UI
        
        Args:
            query_type: Type of query (e.g., "enforce_time_slot")
            params: Parameters like course_id, day, period_start
            input_data: Full scheduling input for validation
        
        Returns:
            List of query constraints
        """
        constraints = []
        
        try:
            qtype = QueryType(query_type)
        except ValueError:
            raise ValueError(f"Unknown query type: {query_type}")
        
        # Validate and create constraint
        if qtype == QueryType.ENFORCE_TIME_SLOT:
            constraint = QueryConstraint(
                query_type=qtype,
                course_id=params.get("course_id"),
                week=params.get("week"),
                day=params.get("day"),
                period_start=params.get("period_start")
            )
            constraints.append(constraint)
        
        elif qtype == QueryType.VETO_DAY:
            # Can apply to course or instructor
            if params.get("course_id"):
                constraint = QueryConstraint(
                    query_type=qtype,
                    course_id=params.get("course_id"),
                    day=params.get("day")
                )
                constraints.append(constraint)
            elif params.get("instructor_id"):
                # Expand instructor-level veto to course-level constraints
                # Find all courses taught by this instructor
                instructor_id = params.get("instructor_id")
                day = params.get("day")
                
                courses = [
                    c for c in input_data.get("courses", [])
                    if c.get("instructor_id") == instructor_id
                ]
                
                if not courses:
                    raise ValueError(f"No courses found for instructor {instructor_id}")
                
                # Create a veto_day constraint for each course
                for course in courses:
                    constraint = QueryConstraint(
                        query_type=qtype,
                        course_id=course["id"],
                        day=day
                    )
                    constraints.append(constraint)
            else:
                raise ValueError("Either course_id or instructor_id required for veto_day")
        
        elif qtype == QueryType.ENFORCE_NO_LUNCH:
            # Get lunch periods from term_config
            term_config = input_data.get("term_config", {})
            lunch_periods = self._get_lunch_periods(term_config)
            
            course_id = params.get("course_id")
            if not course_id:
                raise ValueError("course_id required for enforce_no_lunch")
            
            # Create veto constraint for each lunch period
            for day in term_config.get("days", []):
                for period in lunch_periods:
                    constraint = QueryConstraint(
                        query_type=QueryType.VETO_TIME_SLOT,
                        course_id=course_id,
                        day=day,
                        period_start=period
                    )
                    constraints.append(constraint)
        
        elif qtype == QueryType.VETO_TIME_SLOT:
            # Prevent course from being at specific time slot
            # Week is optional - if not provided, vetoes across all weeks
            constraint = QueryConstraint(
                query_type=qtype,
                course_id=params.get("course_id"),
                week=params.get("week"),  # Optional - None means all weeks
                day=params.get("day"),
                period_start=params.get("period_start")
            )
            constraints.append(constraint)
        
        elif qtype == QueryType.ENFORCE_ROOM:
            # Require course to use specific room at least once
            constraint = QueryConstraint(
                query_type=qtype,
                course_id=params.get("course_id"),
                room_id=params.get("room_id")
            )
            constraints.append(constraint)
        
        elif qtype == QueryType.ENFORCE_BEFORE_TIME:
            constraint = QueryConstraint(
                query_type=qtype,
                course_id=params.get("course_id"),
                period_end=params.get("period_before")
            )
            constraints.append(constraint)
        
        elif qtype == QueryType.ENFORCE_AFTER_TIME:
            constraint = QueryConstraint(
                query_type=qtype,
                course_id=params.get("course_id"),
                period_start=params.get("period_after")
            )
            constraints.append(constraint)
        
        elif qtype == QueryType.SWAP_TIME_SLOTS:
            # Two courses swap their assigned times
            c1 = params.get("course_id_1")
            c2 = params.get("course_id_2")
            
            if not c1 or not c2:
                raise ValueError("Both course_id_1 and course_id_2 required for swap")
            
            # Find current assignments from original schedule
            current_schedule = params.get("current_schedule", {})
            c1_assignment = self._find_assignment(c1, current_schedule)
            c2_assignment = self._find_assignment(c2, current_schedule)
            
            if not c1_assignment or not c2_assignment:
                raise ValueError("Cannot find current assignments for swap")
            
            # Enforce c1 to c2's slot and veto c1 from its current slot
            constraints.append(QueryConstraint(
                query_type=QueryType.ENFORCE_TIME_SLOT,
                course_id=c1,
                week=c2_assignment["week"],
                day=c2_assignment["day"],
                period_start=c2_assignment["period_start"]
            ))
            constraints.append(QueryConstraint(
                query_type=QueryType.VETO_TIME_SLOT,
                course_id=c1,
                week=c1_assignment["week"],
                day=c1_assignment["day"],
                period_start=c1_assignment["period_start"]
            ))
            
            # Enforce c2 to c1's slot and veto c2 from its current slot
            constraints.append(QueryConstraint(
                query_type=QueryType.ENFORCE_TIME_SLOT,
                course_id=c2,
                week=c1_assignment["week"],
                day=c1_assignment["day"],
                period_start=c1_assignment["period_start"]
            ))
            constraints.append(QueryConstraint(
                query_type=QueryType.VETO_TIME_SLOT,
                course_id=c2,
                week=c2_assignment["week"],
                day=c2_assignment["day"],
                period_start=c2_assignment["period_start"]
            ))
        
        elif qtype == QueryType.VETO_INSTRUCTOR_DAY:
            # Veto all courses taught by instructor on a specific day
            instructor_id = params.get("instructor_id")
            day = params.get("day")
            
            if not instructor_id or not day:
                raise ValueError("instructor_id and day required")
            
            # Find all courses taught by this instructor
            courses = [
                c for c in input_data.get("courses", [])
                if c.get("instructor_id") == instructor_id
            ]
            
            for course in courses:
                constraint = QueryConstraint(
                    query_type=QueryType.VETO_DAY,
                    course_id=course["id"],
                    day=day
                )
                constraints.append(constraint)
        
        return constraints
    
    def parse_natural_language(
        self,
        question: str,
        input_data: Dict[str, Any]
    ) -> List[QueryConstraint]:
        """
        Parse a natural language question into query constraints
        
        Examples:
        - "What if MSE252 was scheduled on Monday at 10am?"
        - "Why can't Prof. Anderson avoid teaching on Friday?"
        - "What if all courses avoided lunch hour?"
        
        This uses pattern matching for common queries.
        For complex queries, consider using an LLM for parsing.
        """
        constraints = []
        question_lower = question.lower()
        
        # Extract course IDs
        course_ids = self._extract_course_ids(question, input_data)
        instructor_ids = self._extract_instructor_ids(question, input_data)
        days = self._extract_days(question)
        times = self._extract_times(question)
        
        # Pattern matching for common query types
        if "avoid" in question_lower or "not on" in question_lower:
            if days and course_ids:
                for course_id in course_ids:
                    for day in days:
                        constraints.append(QueryConstraint(
                            query_type=QueryType.VETO_DAY,
                            course_id=course_id,
                            day=day
                        ))
            elif days and instructor_ids:
                for instructor_id in instructor_ids:
                    for day in days:
                        constraints.append(QueryConstraint(
                            query_type=QueryType.VETO_INSTRUCTOR_DAY,
                            instructor_id=instructor_id,
                            day=day
                        ))
        
        elif "lunch" in question_lower:
            term_config = input_data.get("term_config", {})
            lunch_periods = self._get_lunch_periods(term_config)
            
            for course_id in course_ids:
                for day in term_config.get("days", []):
                    for period in lunch_periods:
                        constraints.append(QueryConstraint(
                            query_type=QueryType.VETO_TIME_SLOT,
                            course_id=course_id,
                            day=day,
                            period_start=period
                        ))
        
        elif "before" in question_lower and times:
            for course_id in course_ids:
                constraints.append(QueryConstraint(
                    query_type=QueryType.ENFORCE_BEFORE_TIME,
                    course_id=course_id,
                    period_end=times[0]
                ))
        
        # If no patterns matched, return empty (can integrate LLM here)
        return constraints
    
    def _get_lunch_periods(self, term_config: Dict) -> List[int]:
        """Calculate which periods fall during lunch"""
        day_start = term_config.get("day_start_time", "08:00")
        lunch_start = term_config.get("lunch_start_time", "12:00")
        lunch_end = term_config.get("lunch_end_time", "12:30")
        period_length = term_config.get("period_length_minutes", 30)
        
        start_hour, start_min = map(int, day_start.split(":"))
        lunch_start_hour, lunch_start_min = map(int, lunch_start.split(":"))
        lunch_end_hour, lunch_end_min = map(int, lunch_end.split(":"))
        
        start_minutes = start_hour * 60 + start_min
        lunch_start_minutes = lunch_start_hour * 60 + lunch_start_min
        lunch_end_minutes = lunch_end_hour * 60 + lunch_end_min
        
        lunch_periods = []
        period_index = 0
        current_time = start_minutes
        
        while current_time < lunch_end_minutes:
            period_end = current_time + period_length
            # Check if period overlaps with lunch
            if period_end > lunch_start_minutes and current_time < lunch_end_minutes:
                lunch_periods.append(period_index)
            current_time = period_end
            period_index += 1
        
        return lunch_periods
    
    def _find_assignment(self, course_id: str, schedule: Dict) -> Optional[Dict]:
        """Find a course's assignment in the current schedule"""
        assignments = schedule.get("assignments", [])
        for assignment in assignments:
            if assignment.get("course_id") == course_id:
                return assignment
        return None
    
    def _extract_course_ids(self, text: str, input_data: Dict) -> List[str]:
        """Extract course IDs mentioned in text"""
        course_ids = []
        for course in input_data.get("courses", []):
            course_id = course.get("id", "")
            course_name = course.get("name", "")
            if course_id.lower() in text.lower() or course_name.lower() in text.lower():
                course_ids.append(course_id)
        return course_ids
    
    def _extract_instructor_ids(self, text: str, input_data: Dict) -> List[str]:
        """Extract instructor IDs mentioned in text"""
        instructor_ids = []
        for instructor in input_data.get("instructors", []):
            instructor_id = instructor.get("id", "")
            instructor_name = instructor.get("name", "")
            if instructor_id.lower() in text.lower() or instructor_name.lower() in text.lower():
                instructor_ids.append(instructor_id)
        return instructor_ids
    
    def _extract_days(self, text: str) -> List[str]:
        """Extract day names from text"""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        short_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        found_days = []
        text_lower = text.lower()
        
        for i, day in enumerate(days):
            if day.lower() in text_lower or short_days[i].lower() in text_lower:
                found_days.append(short_days[i])
        
        return found_days
    
    def _extract_times(self, text: str) -> List[int]:
        """Extract time periods from text (returns period indices)"""
        # Simple pattern matching for times like "10am", "2:30pm"
        time_pattern = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?'
        matches = re.findall(time_pattern, text.lower())
        
        periods = []
        for match in matches:
            hour = int(match[0])
            minute = int(match[1]) if match[1] else 0
            meridiem = match[2]
            
            if meridiem == 'pm' and hour < 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0
            
            # Convert to period index (assuming 8am start, 30min periods)
            start_minutes = 8 * 60
            time_minutes = hour * 60 + minute
            period_index = (time_minutes - start_minutes) // 30
            
            if period_index >= 0:
                periods.append(period_index)
        
        return periods
    
    def _build_query_patterns(self) -> Dict[str, str]:
        """Build regex patterns for common query types"""
        return {
            "veto_day": r"(avoid|not on|no classes on)\s+(\w+day)",
            "enforce_time": r"(schedule|put|assign).*?on\s+(\w+day).*?at\s+(\d+)",
            "before_time": r"before\s+(\d+)",
            "after_time": r"after\s+(\d+)",
            "swap": r"swap.*?(\w+).*?and.*?(\w+)"
        }


def validate_query_constraints(
    constraints: List[QueryConstraint],
    input_data: Dict[str, Any]
) -> tuple[bool, List[str]]:
    """
    Validate that query constraints are logically consistent
    
    Returns:
        (is_valid, error_messages)
    """
    errors = []
    
    # Check for contradictory constraints
    enforce_slots = {}
    veto_slots = {}
    
    for constraint in constraints:
        key = (constraint.course_id, constraint.week, constraint.day, constraint.period_start)
        
        if constraint.query_type in [QueryType.ENFORCE_TIME_SLOT, QueryType.ENFORCE_DAY]:
            enforce_slots[key] = constraint
        elif constraint.query_type in [QueryType.VETO_TIME_SLOT, QueryType.VETO_DAY]:
            veto_slots[key] = constraint
    
    # Check for conflicts
    for key in enforce_slots:
        if key in veto_slots:
            errors.append(
                f"Contradictory constraints: Cannot both enforce and veto {key[0]} "
                f"on {key[2]} at period {key[3]}"
            )
    
    # Validate course/instructor IDs exist
    valid_course_ids = {c["id"] for c in input_data.get("courses", [])}
    valid_instructor_ids = {i["id"] for i in input_data.get("instructors", [])}
    
    for constraint in constraints:
        if constraint.course_id and constraint.course_id not in valid_course_ids:
            errors.append(f"Unknown course ID: {constraint.course_id}")
        if constraint.instructor_id and constraint.instructor_id not in valid_instructor_ids:
            errors.append(f"Unknown instructor ID: {constraint.instructor_id}")
    
    return len(errors) == 0, errors


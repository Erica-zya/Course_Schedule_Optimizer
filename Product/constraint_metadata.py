# Comprehensive constraint metadata for explanation generation
CONSTRAINT_METADATA = {
    # Hard Constraints (MUST be satisfied)
    "C1_teacher_conflict": {
        "name": "Instructor Time Conflict",
        "type": "hard",
        "category": "scheduling",
        "description": "An instructor cannot teach two courses at the same time.",
        "user_explanation_template": "Professor {instructor} cannot teach {course1} and {course2} simultaneously at {time}."
    },
    
    "C2_room_conflict": {
        "name": "Room Double-Booking",
        "type": "hard",
        "category": "resources",
        "description": "A room cannot host multiple courses at the same time.",
        "user_explanation_template": "Room {room} is already occupied at {time}."
    },
    
    "C3_hours_requirement": {
        "name": "Course Hours Requirement",
        "type": "hard",
        "category": "requirements",
        "description": "Courses must meet their required weekly hours and session counts.",
        "user_explanation_template": "{course} requires {required_sessions} sessions per week but only has {actual_sessions} sessions scheduled."
    },
    
    "C4_instructor_availability": {
        "name": "Instructor Availability",
        "type": "hard",
        "category": "availability",
        "description": "Courses can only be scheduled when the instructor is available.",
        "user_explanation_template": "Professor {instructor} is not available at {time}."
    },
    
    "C7_room_capacity": {
        "name": "Room Capacity Limit",
        "type": "hard",
        "category": "resources",
        "description": "Course enrollment must not exceed room capacity.",
        "user_explanation_template": "Room {room} has capacity {capacity} but {course} has {enrollment} students."
    },
    
    "C8_one_session_per_day": {
        "name": "One Session Per Day",
        "type": "hard",
        "category": "scheduling",
        "description": "A course cannot be scheduled more than once in a single day.",
        "user_explanation_template": "{course} cannot have multiple sessions on the same day."
    },
    
    "C9_weekly_consistency": {
        "name": "Weekly Pattern Consistency",
        "type": "hard",
        "category": "scheduling",
        "description": "The schedule pattern must be identical across all active weeks within a course's term.",
        "user_explanation_template": "{course} must have the same weekly schedule pattern throughout its term."
    },
    
    # Soft Constraints (minimized in objective)
    "S1_student_conflicts": {
        "name": "Student Schedule Conflicts",
        "type": "soft",
        "category": "student_experience",
        "description": "Students enrolled in multiple courses should not have time conflicts.",
        "user_explanation_template": "{count} students have conflicting courses at the same time.",
        "weight_description": "Each conflict is weighted by the number of affected students and the global weight (w1).",
        "weight_param": "global_student_conflict_weight"
    },
    
    "S2_instructor_compactness": {
        "name": "Instructor Compactness (Back-to-Back Preference)",
        "type": "soft",
        "category": "instructor_preference",
        "description": "Honors instructor preferences for back-to-back teaching or gaps between classes.",
        "user_explanation_template": "{instructor} has {gap_count} gaps in their schedule (preference: {preference}).",
        "weight_description": "Penalty depends on instructor's back-to-back preference: -1 (PREFER consecutive/back-to-back), 0 (neutral), +1 (AVOID consecutive/prefer gaps). When preference is honored, this can generate negative objective values (rewards).",
        "weight_param": "instructor_compactness_weight",
        "preference_encoding": {
            "-1": "Prefer back-to-back (consecutive) teaching",
            "0": "No preference / Neutral",
            "1": "Avoid back-to-back / Prefer gaps between classes"
        }
    },
    
    "S3_preferred_time_slots": {
        "name": "Preferred Time Slots (Avoid Lunch & Evening)",
        "type": "soft",
        "category": "scheduling",
        "description": "Minimize courses scheduled during lunch (12:00-12:30) and evening (after 18:00) time slots.",
        "user_explanation_template": "{count} courses are scheduled during lunch or evening hours.",
        "weight_description": "Each lunch or evening session adds a penalty weighted by w3.",
        "weight_param": "preferred_time_slots_weight"
    }
}


def get_constraint_explanation(constraint_id: str, context: dict = None) -> str:
    """
    Get human-readable explanation for a constraint
    
    Args:
        constraint_id: Constraint identifier (e.g., "C1_teacher_conflict")
        context: Optional dict with values to fill template
    
    Returns:
        Human-readable explanation string
    """
    if constraint_id not in CONSTRAINT_METADATA:
        return f"Unknown constraint: {constraint_id}"
    
    metadata = CONSTRAINT_METADATA[constraint_id]
    
    if context and "user_explanation_template" in metadata:
        try:
            return metadata["user_explanation_template"].format(**context)
        except KeyError:
            return metadata["description"]
    
    return metadata["description"]


def get_constraints_by_type(constraint_type: str) -> dict:
    """Get all constraints of a given type (hard/soft)"""
    return {
        cid: metadata 
        for cid, metadata in CONSTRAINT_METADATA.items() 
        if metadata["type"] == constraint_type
    }

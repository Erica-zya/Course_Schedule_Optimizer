import google.generativeai as genai
from typing import Dict, Any, Optional, List
import json
import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path to import config.py from project root
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from config import Config
from constraint_metadata import CONSTRAINT_METADATA, get_constraint_explanation


class ExplanationAgent:
    """
    LLM-based agent for explaining optimization results
    Uses Google Gemini API
    """
    
    def __init__(self, api_key: str = None):
        """Initialize Gemini API client"""
        api_key = api_key or Config.GEMINI_API_KEY
        genai.configure(api_key=api_key)
        
        self.model = genai.GenerativeModel(
            model_name=Config.GEMINI_MODEL,
            generation_config={
                "temperature": Config.TEMPERATURE,
                "max_output_tokens": Config.MAX_EXPLANATION_TOKENS,
            }
        )
        
        self.system_prompt = self._build_system_prompt()
    
    def explain_schedule(
        self, 
        input_summary: Dict[str, Any],
        solver_output: Dict[str, Any],
        question: str = None,
        full_input: Dict[str, Any] = None
    ) -> str:
        """
        Generate natural language explanation for a schedule
        
        Args:
            input_summary: Summarized input parameters (deprecated, kept for compatibility)
            solver_output: Output from optimization solver
            question: Optional specific question from user
            full_input: Full input JSON with courses, instructors, etc. (required for detailed explanations)
        
        Returns:
            Natural language explanation
        """
        # If full_input not provided, fall back to old generic approach
        if full_input is None:
            print("⚠️ Warning: full_input not provided, generating generic explanation")
            return self._explain_schedule_generic(input_summary, solver_output, question)
        
        # Build rich input context
        input_context = self._build_input_context(full_input)
        
        # Route to specialized explainers based on status
        status = solver_output.get("status")
        
        if status == "infeasible":
            return self._explain_infeasible_schedule(solver_output, input_context, question)
        elif status == "optimal":
            return self._explain_optimal_schedule(solver_output, input_context, question)
        else:
            return self._explain_error_schedule(solver_output, input_context)
    
    def compare_schedules(
        self,
        old_run: Dict[str, Any],
        new_run: Dict[str, Any],
        question: str = None
    ) -> str:
        """
        Explain differences between two schedules
        
        Args:
            old_run: Previous optimization run
            new_run: Current optimization run
            question: Optional specific question
        
        Returns:
            Explanation of changes
        """
        if not question:
            question = "How did the schedule change? What trade-offs were made with the new preferences?"
        
        context = self._build_comparison_context(old_run, new_run)
        
        prompt = f"""{self.system_prompt}

{context}

User Question: {question}

Provide a clear explanation of what changed and why."""
        
        response = self.model.generate_content(prompt)
        return response.text
    
    def _build_input_context(self, full_input: Dict) -> Dict:
        """
        Deeply analyze the input to extract all relevant details for explanations
        
        This creates a rich context object that allows us to reference:
        - Specific course names, instructors, rooms
        - Availability patterns for each instructor
        - Enrollment numbers and capacity constraints
        - Time slot distributions
        """
        term_config = full_input.get("term_config", {})
        
        # Calculate periods per day for time slot mapping
        period_length = term_config.get("period_length_minutes", 30)
        day_start = term_config.get("day_start_time", "08:00")
        
        # Build instructor lookup with detailed availability
        instructors_context = []
        instructors_by_id = {}
        
        for inst in full_input.get("instructors", []):
            available_slots = []
            for slot in inst.get("availability", []):
                time_str = self._period_to_time_string(
                    slot["period_index"], 
                    day_start, 
                    period_length
                )
                available_slots.append({
                    "day": slot["day"],
                    "period": slot["period_index"],
                    "time": time_str
                })
            
            total_hours = len(available_slots) * (period_length / 60.0)
            
            inst_context = {
                "id": inst["id"],
                "name": inst.get("name", inst["id"]),
                "available_slots": available_slots,
                "total_available_hours": total_hours,
                "assigned_courses": [],  # Will populate below
                "preferences": {
                    "back_to_back": inst.get("back_to_back_preference", 0),
                    "allow_lunch": inst.get("allow_lunch_teaching", False)
                }
            }
            instructors_context.append(inst_context)
            instructors_by_id[inst["id"]] = inst_context
        
        # Build course context with instructor and enrollment details
        courses_context = []
        for course in full_input.get("courses", []):
            instructor_id = course.get("instructor_id")
            instructor = instructors_by_id.get(instructor_id)
            
            if instructor:
                instructor["assigned_courses"].append(course["id"])
            
            course_context = {
                "id": course["id"],
                "name": course.get("name", course["id"]),
                "instructor": {
                    "id": instructor_id,
                    "name": instructor["name"] if instructor else instructor_id
                } if instructor else None,
                "weekly_hours": course.get("weekly_hours", 1.5),
                "enrolled_students": course.get("expected_enrollment", 0),
                "type": course.get("type", "full_term")
            }
            courses_context.append(course_context)
        
        # Build room context
        rooms_context = []
        for room in full_input.get("classrooms", []):
            rooms_context.append({
                "id": room["id"],
                "name": room.get("name", room["id"]),
                "capacity": room["capacity"]
            })
        
        # Build students context
        students_context = []
        course_enrollment_map = {}  # course_id -> list of student names
        
        for student in full_input.get("students", []):
            student_context = {
                "id": student["id"],
                "name": student.get("name", student["id"]),
                "enrolled_courses": student.get("enrolled_course_ids", [])
            }
            students_context.append(student_context)
            
            # Build enrollment map for conflict analysis
            for course_id in student.get("enrolled_course_ids", []):
                if course_id not in course_enrollment_map:
                    course_enrollment_map[course_id] = []
                course_enrollment_map[course_id].append(student.get("name", student["id"]))
        
        # Analyze constraint feasibility at input level
        constraints_summary = self._analyze_constraint_feasibility(
            courses_context,
            instructors_context,
            rooms_context,
            term_config
        )
        
        return {
            "courses": courses_context,
            "instructors": instructors_context,
            "rooms": rooms_context,
            "students": students_context,
            "course_enrollment_map": course_enrollment_map,
            "term_config": term_config,
            "constraints_summary": constraints_summary
        }
    
    def _period_to_time_string(self, period_index: int, day_start: str, period_minutes: int) -> str:
        """Convert period index to human-readable time"""
        start_hour, start_min = map(int, day_start.split(":"))
        start_time = datetime(2000, 1, 1, start_hour, start_min)
        
        period_start = start_time + timedelta(minutes=period_index * period_minutes)
        period_end = period_start + timedelta(minutes=period_minutes)
        
        return f"{period_start.strftime('%H:%M')}-{period_end.strftime('%H:%M')}"
    
    def _analyze_constraint_feasibility(
        self,
        courses: List[Dict],
        instructors: List[Dict],
        rooms: List[Dict],
        term_config: Dict
    ) -> Dict:
        """Pre-analyze obvious constraint violations before solver runs"""
        total_required = sum(c["weekly_hours"] for c in courses)
        total_available = sum(i["total_available_hours"] for i in instructors)
        
        period_length = term_config.get("period_length_minutes", 30)
        
        capacity_issues = []
        for course in courses:
            enrollment = course["enrolled_students"]
            max_room_capacity = max([r["capacity"] for r in rooms]) if rooms else 0
            
            if enrollment > max_room_capacity:
                capacity_issues.append({
                    "course": course["name"],
                    "course_id": course["id"],
                    "enrollment": enrollment,
                    "max_room_capacity": max_room_capacity,
                    "deficit": enrollment - max_room_capacity
                })
        
        availability_gaps = []
        consecutive_block_issues = []
        
        for course in courses:
            instructor = next(
                (i for i in instructors if course["instructor"] and i["id"] == course["instructor"]["id"]),
                None
            )
            if instructor:
                required = course["weekly_hours"]
                available = instructor["total_available_hours"]
                
                # Check total hours
                if available < required:
                    availability_gaps.append({
                        "course": course["name"],
                        "course_id": course["id"],
                        "instructor": instructor["name"],
                        "instructor_id": instructor["id"],
                        "required_hours": required,
                        "available_hours": available,
                        "deficit": required - available
                    })
                else:
                    # Check if consecutive blocks exist
                    required_periods = int((required * 60) / period_length)
                    has_consecutive = self._check_consecutive_availability(
                        instructor["available_slots"],
                        required_periods
                    )
                    
                    if not has_consecutive:
                        consecutive_block_issues.append({
                            "course": course["name"],
                            "course_id": course["id"],
                            "instructor": instructor["name"],
                            "instructor_id": instructor["id"],
                            "required_hours": required,
                            "required_consecutive_periods": required_periods,
                            "available_slots": instructor["available_slots"][:5]  # Show first 5 as examples
                        })
        
        return {
            "total_required_hours": total_required,
            "total_available_instructor_hours": total_available,
            "capacity_issues": capacity_issues,
            "availability_gaps": availability_gaps,
            "consecutive_block_issues": consecutive_block_issues
        }
    
    def _check_consecutive_availability(self, available_slots: List[Dict], required_periods: int) -> bool:
        """Check if instructor has any consecutive block of required length"""
        # Group slots by day
        by_day = {}
        for slot in available_slots:
            day = slot["day"]
            period = slot["period"]
            if day not in by_day:
                by_day[day] = []
            by_day[day].append(period)
        
        # Check each day for consecutive blocks
        for day, periods in by_day.items():
            sorted_periods = sorted(periods)
            consecutive_count = 1
            
            for i in range(1, len(sorted_periods)):
                if sorted_periods[i] == sorted_periods[i-1] + 1:
                    consecutive_count += 1
                    if consecutive_count >= required_periods:
                        return True
                else:
                    consecutive_count = 1
        
        return False
    
    def _explain_infeasible_schedule(
        self,
        solver_output: Dict,
        input_context: Dict,
        question: str = None
    ) -> str:
        """Generate conversational explanation for infeasible schedules"""
        diagnostics = solver_output.get("diagnostics", {})
        constraints_summary = input_context["constraints_summary"]
        
        # Build narrative components
        problem_narrative = self._build_infeasibility_narrative(
            input_context,
            constraints_summary,
            diagnostics
        )
        
        # Generate conversational explanation via Gemini
        prompt = f"""You are a scheduling expert explaining why a course schedule is infeasible.

SETUP:
- {len(input_context['courses'])} courses, {len(input_context['instructors'])} instructors, {len(input_context['rooms'])} classrooms

COURSES:
{self._format_courses_for_prompt(input_context['courses'][:5])}

INSTRUCTORS:
{self._format_instructors_for_prompt(input_context['instructors'][:5])}

PROBLEM:
{problem_narrative}

YOUR TASK:
Write a concise explanation (2-3 short paragraphs) that explains WHAT'S WRONG and WHY. Be direct and specific.

RULES:
- Use actual names: "MSE252 (Decision Analysis)", "Prof. Anderson", "Smith Hall 101"
- Use actual numbers: "60 students", "1.5 hours", "3 consecutive periods"
- Be concise - no fluff or generic statements
- NO SUGGESTIONS or "to fix this" recommendations - ONLY explain what's wrong
- Avoid jargon: don't say "IIS", "constraint", "infeasibility"
- Write in flowing paragraphs, NOT bullet points

Now explain what's preventing the schedule:"""
        
        try:
            response = self.model.generate_content(prompt)
            
            # Check if response has valid content
            if not response or not response.text:
                print("⚠️ Gemini returned empty response, using fallback")
                return self._build_infeasible_fallback_explanation(input_context, constraints_summary)
            
            return response.text
            
        except Exception as e:
            error_str = str(e)
            print(f"⚠️ Gemini API error: {error_str}")
            
            # If it's a safety/content filter issue, use fallback
            if "finish_reason" in error_str or "safety" in error_str.lower() or "blocked" in error_str.lower():
                print("   Detected safety filter or content block, using fallback explanation")
                return self._build_infeasible_fallback_explanation(input_context, constraints_summary)
            
            # Re-raise other errors
            raise
    
    def _build_infeasible_fallback_explanation(
        self,
        input_context: Dict,
        constraints_summary: Dict
    ) -> str:
        """
        Generate a human-readable infeasibility explanation without using Gemini API
        Used as fallback when Gemini API fails or is blocked
        """
        paragraphs = []
        
        # Opening - concise
        num_courses = len(input_context['courses'])
        paragraphs.append(
            f"No valid schedule exists for {num_courses} course(s). Here's what's preventing a solution:"
        )
        
        # Analyze specific issues - be direct
        capacity_issues = constraints_summary.get("capacity_issues", [])
        availability_gaps = constraints_summary.get("availability_gaps", [])
        consecutive_block_issues = constraints_summary.get("consecutive_block_issues", [])
        
        # Room capacity issues
        if capacity_issues:
            for issue in capacity_issues:
                paragraphs.append(
                    f"**Room Capacity:** {issue['course']} has {issue['enrollment']} students but the largest "
                    f"classroom holds only {issue['max_room_capacity']} ({issue['deficit']} seat deficit)."
                )
        
        # Instructor availability issues
        if availability_gaps:
            for gap in availability_gaps:
                paragraphs.append(
                    f"**Insufficient Hours:** {gap['course']} requires {gap['required_hours']} hours per week, "
                    f"but {gap['instructor']} is available for only {gap['available_hours']} hours "
                    f"({gap['deficit']} hour shortfall)."
                )
        
        # Consecutive block issues
        if consecutive_block_issues:
            for issue in consecutive_block_issues:
                periods_needed = issue['required_consecutive_periods']
                slots_str = ", ".join([f"{s['day']} {s['time']}" for s in issue['available_slots'][:3]])
                if len(issue['available_slots']) > 3:
                    slots_str += "..."
                
                paragraphs.append(
                    f"**Scattered Time Slots:** {issue['course']} needs {issue['required_hours']} hours "
                    f"({periods_needed} consecutive 30-minute periods) in a single unbroken block. "
                    f"{issue['instructor']}'s available slots ({slots_str}) are scattered across different days. "
                    f"The course requires all {periods_needed} periods to be consecutive on the same day."
                )
        
        # Overall hours mismatch
        total_req = constraints_summary.get("total_required_hours", 0)
        total_avail = constraints_summary.get("total_available_instructor_hours", 0)
        
        if total_req > total_avail and not availability_gaps and not consecutive_block_issues:
            paragraphs.append(
                f"**Overall Hours:** All courses need {total_req} hours per week total, "
                f"but instructors are available for only {total_avail} hours ({total_req - total_avail} hour deficit)."
            )
        
        # If no specific issues found
        if len(paragraphs) == 1:  # Only opening paragraph
            paragraphs.append(
                "The optimizer detected constraint conflicts but couldn't isolate a single root cause. "
                "This indicates complex interactions between timing constraints, consecutive session requirements, "
                "and weekly pattern consistency rules."
            )
        
        return "\n\n".join(paragraphs)
    
    def _build_infeasibility_narrative(
        self,
        input_context: Dict,
        constraints_summary: Dict,
        diagnostics: Dict
    ) -> str:
        """Build a narrative of what went wrong using actual data"""
        narrative_parts = []
        
        # Check capacity issues
        capacity_issues = constraints_summary.get("capacity_issues", [])
        if capacity_issues:
            for issue in capacity_issues:
                narrative_parts.append(
                    f"CAPACITY PROBLEM: {issue['course']} has {issue['enrollment']} students enrolled, "
                    f"but your largest classroom only holds {issue['max_room_capacity']} students. "
                    f"This is a deficit of {issue['deficit']} seats."
                )
        
        # Check availability gaps
        availability_gaps = constraints_summary.get("availability_gaps", [])
        if availability_gaps:
            for gap in availability_gaps:
                narrative_parts.append(
                    f"AVAILABILITY PROBLEM: {gap['course']} requires {gap['required_hours']} hours per week, "
                    f"but {gap['instructor']} is only available for {gap['available_hours']} hours total. "
                    f"This is a shortfall of {gap['deficit']} hours."
                )
        
        # Check consecutive block issues
        consecutive_block_issues = constraints_summary.get("consecutive_block_issues", [])
        if consecutive_block_issues:
            for issue in consecutive_block_issues:
                slots_str = ", ".join([f"{s['day']} {s['time']}" for s in issue['available_slots'][:3]])
                if len(issue['available_slots']) > 3:
                    slots_str += "..."
                narrative_parts.append(
                    f"CONSECUTIVE BLOCK PROBLEM: {issue['course']} needs {issue['required_hours']} hours "
                    f"({issue['required_consecutive_periods']} consecutive periods) in a single unbroken block. "
                    f"{issue['instructor']}'s available slots ({slots_str}) are scattered across different days—"
                    f"not consecutive on any single day."
                )
        
        # Check overall hours balance
        total_req = constraints_summary.get("total_required_hours", 0)
        total_avail = constraints_summary.get("total_available_instructor_hours", 0)
        
        if total_req > total_avail:
            narrative_parts.append(
                f"OVERALL HOURS MISMATCH: All courses together need {total_req} hours per week, "
                f"but instructors are only available for {total_avail} hours total. "
                f"You're short by {total_req - total_avail} hours."
            )
        
        # If no specific issues found
        if not narrative_parts:
            narrative_parts.append(
                "The solver determined the problem is infeasible, but no specific bottleneck was identified through pre-analysis. "
                "This might indicate a complex interaction between multiple constraints (e.g., timing conflicts, consecutive session requirements)."
            )
        
        return "\n\n".join(narrative_parts)
    
    def _explain_optimal_schedule(
        self,
        solver_output: Dict,
        input_context: Dict,
        question: str = None
    ) -> str:
        """Generate conversational explanation for optimal schedules"""
        assignments = solver_output.get("schedule", {}).get("assignments", [])
        objective_value = solver_output.get("objective_value", 0)
        soft_summary = solver_output.get("soft_constraint_summary", {})
        diagnostics = solver_output.get("diagnostics", {})
        
        # Extract specific conflicts
        student_conflicts = diagnostics.get("student_conflicts", [])
        lunch_violations = diagnostics.get("lunch_violations", [])
        
        # Build analysis
        analysis = self._analyze_optimal_solution(
            assignments,
            student_conflicts,
            lunch_violations,
            input_context,
            soft_summary
        )
        
        # Generate conversational explanation
        prompt = f"""You are a scheduling expert explaining a course schedule result.

SETUP:
- {len(input_context['courses'])} courses, {len(input_context['instructors'])} instructors, {len(assignments)} sessions
- Objective: {objective_value:.1f} (negative = rewards earned)

BREAKDOWN:
- S1 (Student Conflicts): {soft_summary.get('S1_student_conflicts', {}).get('weighted_penalty', 0):.1f}
- S2 (Instructor Back-to-Back): {soft_summary.get('S2_instructor_compactness', {}).get('weighted_penalty', 0):.1f}
- S3 (Lunch/Evening): {soft_summary.get('S3_preferred_time_slots', {}).get('weighted_penalty', 0):.1f}

DETAILS:
{analysis}

YOUR TASK:
Write a concise explanation (2-3 short paragraphs) of the schedule quality. Be direct and specific.

RULES:
- Use actual data: course names, instructor names, specific numbers
- Be concise - no fluff
- Explain what the objective value means
- If negative objective: explain it's a REWARD (good thing)
- NO SUGGESTIONS - ONLY explain what the schedule achieved
- Write in flowing paragraphs, NOT bullet points
- Back-to-back preference: -1 = PREFER consecutive, 1 = AVOID consecutive

Explain the schedule quality:"""
        
        try:
            response = self.model.generate_content(prompt)
            
            # Check if response has valid content
            if not response or not response.text:
                print("⚠️ Gemini returned empty response, using fallback")
                return self._build_optimal_fallback_explanation(
                    input_context, objective_value, soft_summary, 
                    student_conflicts, lunch_violations, assignments
                )
            
            return response.text
            
        except Exception as e:
            error_str = str(e)
            print(f"⚠️ Gemini API error: {error_str}")
            
            # If it's a safety/content filter issue, use fallback
            if "finish_reason" in error_str or "safety" in error_str.lower() or "blocked" in error_str.lower():
                print("   Detected safety filter or content block, using fallback explanation")
                return self._build_optimal_fallback_explanation(
                    input_context, objective_value, soft_summary, 
                    student_conflicts, lunch_violations, assignments
                )
            
            # Re-raise other errors
            raise
    
    def _build_optimal_fallback_explanation(
        self,
        input_context: Dict,
        objective_value: float,
        soft_summary: Dict,
        student_conflicts: List[Dict],
        lunch_violations: List[Dict],
        assignments: List[Dict]
    ) -> str:
        """
        Generate a human-readable optimal schedule explanation without using Gemini API
        Used as fallback when Gemini API fails or is blocked
        """
        paragraphs = []
        
        # Opening - concise
        num_courses = len(input_context['courses'])
        num_sessions = len(assignments)
        
        if objective_value < 0:
            paragraphs.append(
                f"Optimal schedule found for {num_courses} course(s) across {num_sessions} sessions. "
                f"Objective: {objective_value:.1f} (negative = earned rewards by honoring preferences)."
            )
        else:
            paragraphs.append(
                f"Optimal schedule found for {num_courses} course(s) across {num_sessions} sessions. "
                f"Objective: {objective_value:.1f}."
            )
        
        # Analyze each soft constraint - be direct
        s1_penalty = soft_summary.get('S1_student_conflicts', {}).get('weighted_penalty', 0)
        s2_penalty = soft_summary.get('S2_instructor_compactness', {}).get('weighted_penalty', 0)
        s3_penalty = soft_summary.get('S3_preferred_time_slots', {}).get('weighted_penalty', 0)
        
        detail_points = []
        
        # S1: Student conflicts
        num_conflicts = len(student_conflicts)
        total_students = len(input_context['students'])
        
        if num_conflicts == 0:
            detail_points.append(
                f"**Student Conflicts:** None. All {total_students} students have conflict-free schedules."
            )
        else:
            conflict_rate = (num_conflicts / total_students * 100) if total_students > 0 else 0
            detail_points.append(
                f"**Student Conflicts:** {num_conflicts}/{total_students} students ({conflict_rate:.1f}%) have overlapping courses."
            )
        
        # S2: Instructor back-to-back
        if s2_penalty < -5:
            detail_points.append(
                f"**Instructor Preferences:** +{abs(s2_penalty):.1f} reward. "
                f"Instructors who prefer back-to-back classes got them; those who prefer gaps got them."
            )
        elif s2_penalty < 0:
            detail_points.append(
                f"**Instructor Preferences:** +{abs(s2_penalty):.1f} reward from honoring back-to-back preferences."
            )
        elif s2_penalty > 5:
            detail_points.append(
                f"**Instructor Preferences:** {s2_penalty:.1f} penalty. "
                f"Some instructors didn't get their preferred teaching patterns."
            )
        else:
            detail_points.append(
                f"**Instructor Preferences:** {s2_penalty:.1f} (neutral impact)."
            )
        
        # S3: Lunch and evening slots
        num_lunch = len(lunch_violations)
        if num_lunch == 0:
            detail_points.append(
                f"**Time Slots:** No courses during lunch (12:00-12:30) or evening hours."
            )
        else:
            detail_points.append(
                f"**Time Slots:** {num_lunch} session(s) scheduled during lunch or evening hours."
            )
        
        paragraphs.extend(detail_points)
        
        return "\n\n".join(paragraphs)
    
    def _analyze_optimal_solution(
        self,
        assignments: List[Dict],
        student_conflicts: List[Dict],
        lunch_violations: List[Dict],
        input_context: Dict,
        soft_summary: Dict
    ) -> str:
        """Analyze optimal solution and build narrative with specific examples"""
        analysis_parts = []
        
        # Analyze student conflicts
        num_conflicts = len(student_conflicts)
        total_students = len(input_context["students"])
        
        if num_conflicts == 0:
            analysis_parts.append(
                f"STUDENT CONFLICTS: None! All {total_students} students have conflict-free schedules."
            )
        else:
            # Get specific examples
            example_conflicts = student_conflicts[:3]
            conflict_details = []
            
            for conflict in example_conflicts:
                student_id = conflict.get("student_id", "Unknown")
                course1_id = conflict.get("course1_id", "?")
                course2_id = conflict.get("course2_id", "?")
                
                # Find student and course names
                student = next((s for s in input_context["students"] if s["id"] == student_id), None)
                course1 = next((c for c in input_context["courses"] if c["id"] == course1_id), None)
                course2 = next((c for c in input_context["courses"] if c["id"] == course2_id), None)
                
                student_name = student["name"] if student else student_id
                course1_name = course1["name"] if course1 else course1_id
                course2_name = course2["name"] if course2 else course2_id
                
                conflict_details.append(
                    f"{student_name} has {course1_name} and {course2_name} overlapping"
                )
            
            examples_str = "; ".join(conflict_details)
            
            analysis_parts.append(
                f"STUDENT CONFLICTS: {num_conflicts} out of {total_students} students have conflicts. "
                f"Examples: {examples_str}."
            )
        
        # Analyze lunch violations
        if not lunch_violations:
            analysis_parts.append("LUNCH SCHEDULING: Success! No courses scheduled during lunch hours (12:00-12:30).")
        else:
            lunch_courses = [lv.get("course_id", "?") for lv in lunch_violations]
            analysis_parts.append(
                f"LUNCH SCHEDULING: {len(lunch_violations)} courses had to be scheduled during lunch: {', '.join(lunch_courses)}."
            )
        
        # Analyze instructor patterns from soft_summary
        s2_penalty = soft_summary.get('S2_instructor_compactness', {}).get('weighted_penalty', 0)
        if s2_penalty < 0:
            analysis_parts.append(
                f"INSTRUCTOR PREFERENCES: EXCELLENT! Earned {abs(s2_penalty):.1f} points reward by honoring instructor back-to-back preferences. "
                "Instructors who prefer consecutive classes got them."
            )
        elif s2_penalty > 0:
            analysis_parts.append(
                f"INSTRUCTOR PREFERENCES: Incurred {s2_penalty:.1f} points penalty. "
                "Some instructors who prefer gaps between classes ended up with back-to-back sessions, or vice versa."
            )
        else:
            analysis_parts.append("INSTRUCTOR PREFERENCES: Neutral—no significant impact on instructor preferences.")
        
        return "\n\n".join(analysis_parts)
    
    def _format_courses_for_prompt(self, courses: List[Dict]) -> str:
        """Format course list for Gemini prompt"""
        lines = []
        for c in courses:
            instructor_name = c["instructor"]["name"] if c["instructor"] else "Unassigned"
            lines.append(
                f"- {c['id']} ({c['name']}): {c['weekly_hours']} hours/week, "
                f"{c['enrolled_students']} students, taught by {instructor_name}"
            )
        return "\n".join(lines)
    
    def _format_instructors_for_prompt(self, instructors: List[Dict]) -> str:
        """Format instructor availability for Gemini prompt"""
        lines = []
        for inst in instructors:
            num_slots = len(inst["available_slots"])
            hours = inst["total_available_hours"]
            
            # Show first few time slots as examples
            example_slots = inst["available_slots"][:3]
            slots_str = ", ".join([f"{s['day']} {s['time']}" for s in example_slots])
            
            if num_slots > 3:
                slots_str += f" ... and {num_slots - 3} more slots"
            
            courses_str = ", ".join(inst["assigned_courses"]) if inst["assigned_courses"] else "none"
            
            # Decode back-to-back preference
            b2b_pref = inst["preferences"]["back_to_back"]
            if b2b_pref == -1:
                pref_str = "PREFERS back-to-back classes"
            elif b2b_pref == 1:
                pref_str = "AVOIDS back-to-back classes"
            else:
                pref_str = "neutral on back-to-back"
            
            lines.append(
                f"- {inst['name']}: Available for {hours} hours ({num_slots} slots: {slots_str}). "
                f"Preference: {pref_str}. Assigned courses: {courses_str}"
            )
        return "\n".join(lines)
    
    def _explain_error_schedule(self, solver_output: Dict, input_context: Dict) -> str:
        """Handle error cases"""
        error_msg = solver_output.get("diagnostics", {}).get("error", "Unknown error occurred")
        return f"Unfortunately, the scheduling system encountered an error: {error_msg}. Please check your input data and try again."
    
    def _explain_schedule_generic(
        self, 
        input_summary: Dict[str, Any],
        solver_output: Dict[str, Any],
        question: str = None
    ) -> str:
        """Fallback to old generic explanation (deprecated)"""
        if not question:
            if solver_output["status"] == "optimal":
                question = "Explain this schedule: Is it feasible? How were the objectives optimized?"
            elif solver_output["status"] == "infeasible":
                question = "Why is this schedule infeasible? What constraints are conflicting?"
            else:
                question = "Explain this scheduling result."
        
        context = self._build_single_run_context(input_summary, solver_output)
        
        prompt = f"""{self.system_prompt}

{context}

User Question: {question}

Please provide a clear, structured explanation."""
        
        response = self.model.generate_content(prompt)
        return response.text
    
    def _build_system_prompt(self) -> str:
        """Create system prompt for the explanation agent"""
        
        hard_constraints = [
            f"- {cid}: {meta['description']}"
            for cid, meta in CONSTRAINT_METADATA.items()
            if meta['type'] == 'hard'
        ]
        
        soft_constraints = [
            f"- {cid}: {meta['description']}"
            for cid, meta in CONSTRAINT_METADATA.items()
            if meta['type'] == 'soft'
        ]
        
        return f"""You are an expert explanation agent for a university course scheduling optimization system.

CONSTRAINT TYPES:

Hard Constraints (MUST be satisfied for feasibility):
{chr(10).join(hard_constraints)}

Soft Constraints (minimized in the objective function):
{chr(10).join(soft_constraints)}

SOFT CONSTRAINT WEIGHTS (adjustable by user):
- w1 (global_student_conflict_weight): Penalty for student schedule conflicts
- w2 (instructor_compactness_weight): Penalty for gaps between an instructor's classes (respects back-to-back preferences)
- w3 (preferred_time_slots_weight): Penalty for scheduling during lunch (12:00-12:30) or evening (after 18:00)

YOUR ROLE:
- Explain optimization results in clear, conversational language
- Help users understand feasibility and optimality
- When schedules are infeasible, identify the specific conflicting constraints
- When comparing schedules, highlight key changes and trade-offs
- Use concrete examples (course names, times, instructors) rather than abstract concepts
- Explain how soft constraint weights affect the schedule

EXPLANATION STRUCTURE:
1. High-level summary (1-2 sentences)
2. Key findings (bullet points if multiple points)
3. Supporting details (as needed based on question)

TONE:
- Professional but accessible
- Avoid mathematical jargon unless asked
- Be specific: use course IDs, instructor names, times
- If uncertain about details, acknowledge it

SPECIAL CASES:
- Infeasible: Focus on the IIS (irreducible infeasible subsystem) - the minimal set of conflicting constraints
- Optimal: Explain the objective value and main soft constraint trade-offs, including which weights had the most impact
- Comparison: Highlight what changed, why it changed, and what the impact is on soft constraint penalties"""
    
    def _build_single_run_context(
        self, 
        input_summary: Dict[str, Any],
        solver_output: Dict[str, Any]
    ) -> str:
        """Build context string for single schedule explanation"""
        
        context_parts = [
            "=== SCHEDULING PROBLEM ===",
            f"Courses: {input_summary.get('num_courses', 'N/A')}",
            f"Instructors: {input_summary.get('num_instructors', 'N/A')}",
            f"Students: {input_summary.get('num_students', 'N/A')}",
            f"Classrooms: {input_summary.get('num_classrooms', 'N/A')}",
            f"Term Length: {input_summary.get('term_weeks', 'N/A')} weeks",
            "",
            "=== OPTIMIZATION RESULT ===",
            f"Status: {solver_output['status'].upper()}",
        ]
        
        if solver_output['status'] == 'optimal':
            context_parts.extend([
                f"Objective Value: {solver_output['objective_value']:.2f}",
                f"Solve Time: {solver_output.get('solve_time_seconds', 'N/A')} seconds",
                "",
                "=== SOFT CONSTRAINT SUMMARY ===",
                json.dumps(solver_output.get('soft_constraint_summary', {}), indent=2),
                "",
                "=== DIAGNOSTICS ===",
                json.dumps(solver_output.get('diagnostics', {}), indent=2)
            ])
        elif solver_output['status'] == 'infeasible':
            context_parts.extend([
                "",
                "=== INFEASIBILITY ANALYSIS ===",
                f"Violated Hard Constraints: {', '.join(solver_output.get('violated_hard_constraints', []))}",
                "",
                "Irreducible Infeasible Subsystem (IIS):",
                json.dumps(solver_output.get('diagnostics', {}).get('iis', []), indent=2),
                "",
                "Detailed Diagnostics:",
                json.dumps(solver_output.get('diagnostics', {}), indent=2)
            ])
        
        return "\n".join(context_parts)
    
    def _build_comparison_context(
        self,
        old_run: Dict[str, Any],
        new_run: Dict[str, Any]
    ) -> str:
        """Build context for comparing two schedules"""
        
        old_output = old_run['output']
        new_output = new_run['output']
        
        # Calculate objective change
        obj_change = "N/A"
        if old_output.get('objective_value') and new_output.get('objective_value'):
            old_obj = old_output['objective_value']
            new_obj = new_output['objective_value']
            obj_change = f"{old_obj:.2f} → {new_obj:.2f} (Δ = {new_obj - old_obj:+.2f})"
        
        # Find assignment changes
        changes = self._compute_assignment_changes(old_output, new_output)
        
        context_parts = [
            "=== SCHEDULE COMPARISON ===",
            f"Previous Status: {old_output['status']}",
            f"New Status: {new_output['status']}",
            f"Objective Change: {obj_change}",
            "",
            "=== CHANGED ASSIGNMENTS ===",
            json.dumps(changes, indent=2),
            "",
            "=== PREVIOUS SOFT CONSTRAINTS ===",
            json.dumps(old_output.get('soft_constraint_summary', {}), indent=2),
            "",
            "=== NEW SOFT CONSTRAINTS ===",
            json.dumps(new_output.get('soft_constraint_summary', {}), indent=2),
            "",
            "=== PREVIOUS DIAGNOSTICS ===",
            json.dumps(old_output.get('diagnostics', {}), indent=2),
            "",
            "=== NEW DIAGNOSTICS ===",
            json.dumps(new_output.get('diagnostics', {}), indent=2)
        ]
        
        return "\n".join(context_parts)
    
    def _compute_assignment_changes(
        self,
        old_output: Dict[str, Any],
        new_output: Dict[str, Any]
    ) -> list:
        """Compare two schedules and find changed assignments"""
        
        old_assignments = {
            a['course_id']: a 
            for a in old_output.get('schedule', {}).get('assignments', [])
        }
        
        new_assignments = {
            a['course_id']: a 
            for a in new_output.get('schedule', {}).get('assignments', [])
        }
        
        changes = []
        
        for course_id in set(old_assignments.keys()) | set(new_assignments.keys()):
            if course_id not in old_assignments:
                changes.append({
                    "course": course_id,
                    "change_type": "added",
                    "new": self._format_assignment(new_assignments[course_id])
                })
            elif course_id not in new_assignments:
                changes.append({
                    "course": course_id,
                    "change_type": "removed",
                    "old": self._format_assignment(old_assignments[course_id])
                })
            else:
                old_a = old_assignments[course_id]
                new_a = new_assignments[course_id]
                
                if (old_a.get('day') != new_a.get('day') or 
                    old_a.get('period_start') != new_a.get('period_start') or
                    old_a.get('room_id') != new_a.get('room_id')):
                    
                    changes.append({
                        "course": course_id,
                        "change_type": "modified",
                        "old": self._format_assignment(old_a),
                        "new": self._format_assignment(new_a)
                    })
        
        return changes
    
    def _format_assignment(self, assignment: Dict[str, Any]) -> str:
        """Format assignment as readable string"""
        return (f"{assignment.get('day', '?')} period {assignment.get('period_start', '?')}"
                f" in {assignment.get('room_id', '?')}")
    
    def explain_what_if_result(
        self,
        what_if_result: Dict[str, Any],
        query_description: str,
        input_context: Dict[str, Any]
    ) -> str:
        """
        Generate explanation for a what-if query result
        
        Args:
            what_if_result: Output from solve_what_if
            query_description: Natural language description of the query
            input_context: Full input data for context
        
        Returns:
            Natural language explanation
        """
        status = what_if_result.get("status")
        
        if status == "feasible_query":
            return self._explain_feasible_what_if(what_if_result, query_description, input_context)
        elif status == "infeasible_query":
            return self._explain_infeasible_what_if(what_if_result, query_description, input_context)
        else:
            return f"What-if analysis status: {status}. {what_if_result.get('explanation', '')}"
    
    def _explain_feasible_what_if(
        self,
        result: Dict[str, Any],
        query: str,
        input_context: Dict[str, Any]
    ) -> str:
        """Explain a feasible what-if scenario"""
        original_obj = result.get("original_objective", 0)
        alternative_obj = result.get("alternative_objective", 0)
        diff = result.get("objective_difference", 0)
        
        if diff == 0:
            return (
                f"✅ **Alternative Found (No Cost)**\n\n"
                f"Your scenario: *{query}*\n\n"
                f"This change is possible without any increase in soft constraint penalties! "
                f"The alternative schedule achieves the same objective value ({alternative_obj:.1f})."
            )
        else:
            return (
                f"✅ **Alternative Found (With Trade-offs)**\n\n"
                f"Your scenario: *{query}*\n\n"
                f"This change is possible, but increases soft constraint penalties by {abs(diff):.1f} "
                f"(from {original_obj:.1f} to {alternative_obj:.1f}). "
                f"Check the alternative schedule to see what changed."
            )
    
    def _explain_infeasible_what_if(
        self,
        result: Dict[str, Any],
        query: str,
        input_context: Dict[str, Any]
    ) -> str:
        """
        Explain an infeasible what-if scenario using IIS
        Based on X-MILP paper Section 4.4: Graph of Reasons
        """
        iis_constraints = result.get("iis", [])
        iis_summary = result.get("iis_summary", {})
        
        # Build graph of reasons
        graph = self.build_graph_of_reasons(iis_constraints, query, input_context)
        
        # Generate narrative explanation via Gemini
        try:
            prompt = f"""You are explaining why a scheduling change is infeasible.

USER'S DESIRED SCENARIO: {query}

WHY IT'S INFEASIBLE:
The optimizer found the minimal set of conflicting constraints (IIS - Irreducible Infeasible Subsystem):

{self._format_iis_for_llm(iis_constraints, iis_summary)}

CONTEXT:
- Original optimal objective: {result.get('original_objective', 'N/A')}
- The scenario requires the new schedule to be at least as good as the original
- The IIS shows the {len(iis_constraints)} constraints that make this infeasible

YOUR TASK:
Write a clear, conversational explanation (2-3 short paragraphs) explaining:
1. What the user wants to change
2. Why it's infeasible (causally - show the chain of constraints)
3. Which constraints are the root cause

RULES:
- Use the actual constraint descriptions provided
- Explain cause-and-effect relationships between constraints
- Be specific and concrete - no generic platitudes
- NO SUGGESTIONS for fixes - only explain what's blocking
- Write in flowing paragraphs, NOT bullet points

Explain why this scenario is infeasible:"""
            
            response = self.model.generate_content(prompt)
            
            if not response or not response.text:
                return self._build_infeasible_what_if_fallback(result, query, iis_constraints, iis_summary)
            
            return response.text
            
        except Exception as e:
            print(f"⚠️ Gemini API error in what-if explanation: {e}")
            return self._build_infeasible_what_if_fallback(result, query, iis_constraints, iis_summary)
    
    def _build_infeasible_what_if_fallback(
        self,
        result: Dict[str, Any],
        query: str,
        iis_constraints: List[Dict],
        iis_summary: Dict
    ) -> str:
        """Fallback explanation when Gemini API fails"""
        explanation_parts = [
            f"❌ **Infeasible Scenario**\n",
            f"Your scenario: *{query}*\n",
            f"This scenario cannot achieve an objective value ≤ {result.get('original_objective', 'N/A')}.\n"
        ]
        
        # Explain IIS
        has_minimality = iis_summary.get("minimality_in_iis", False)
        num_query = iis_summary.get("num_query_constraints_in_iis", 0)
        
        if has_minimality:
            explanation_parts.append(
                f"\n**Why:** The minimality constraint (requiring objective ≤ original) conflicts with your scenario. "
                f"This means your desired changes would make the schedule worse in terms of soft constraint penalties."
            )
        
        if num_query > 0:
            explanation_parts.append(
                f"\n**Conflicting Constraints:** {num_query} of your query constraints directly conflict with "
                f"existing hard constraints (like instructor availability, room capacity, or scheduling patterns)."
            )
        
        # List IIS constraints
        if iis_constraints:
            explanation_parts.append("\n**Minimal Conflicting Set (IIS):**")
            for constraint in iis_constraints[:5]:  # Show up to 5
                desc = constraint.get("description", "Unknown constraint")
                explanation_parts.append(f"- {desc}")
            
            if len(iis_constraints) > 5:
                explanation_parts.append(f"- ... and {len(iis_constraints) - 5} more constraints")
        
        return "\n".join(explanation_parts)
    
    def build_graph_of_reasons(
        self,
        iis_constraints: List[Dict],
        query: str,
        input_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build graph of reasons from IIS
        Based on X-MILP paper Section 4.4
        
        Returns a structured representation showing:
        - Nodes: Individual constraints (reasons)
        - Edges: Relationships between constraints
        - Natural language labels for each reason
        """
        reasons = []
        edges = []
        
        for i, constraint in enumerate(iis_constraints):
            constraint_id = constraint.get("id", f"c{i}")
            constraint_type = constraint.get("type", "unknown")
            description = constraint.get("description", "Unknown constraint")
            
            # Convert to natural language reason
            reason = {
                "id": constraint_id,
                "type": constraint_type,
                "text": self._constraint_to_reason(constraint, input_context),
                "in_iis": constraint.get("in_iis", True)
            }
            reasons.append(reason)
            
            # Build edges (relationships between constraints)
            # Two constraints are related if they share variables (course, instructor, time)
            for j, other_constraint in enumerate(iis_constraints[i+1:], start=i+1):
                if self._constraints_share_scope(constraint, other_constraint):
                    edges.append({
                        "from": constraint_id,
                        "to": other_constraint.get("id", f"c{j}"),
                        "relationship": "shares_variables"
                    })
        
        return {
            "query": query,
            "reasons": reasons,
            "edges": edges,
            "num_reasons": len(reasons),
            "is_connected": len(edges) > 0,
            "visualization_data": self._create_graph_visualization_json(reasons, edges)
        }
    
    def _constraint_to_reason(self, constraint: Dict, input_context: Dict) -> str:
        """Convert a constraint to natural language reason"""
        constraint_type = constraint.get("type", "")
        description = constraint.get("description", "")
        
        # Map constraint types to natural language templates
        if constraint_type == "minimality":
            return "The new schedule must achieve at least the same objective value as the original optimal schedule"
        elif constraint_type.startswith("query_"):
            return f"Your requested change: {description}"
        elif constraint_type == "enforce_time_slot":
            return f"Required: {description}"
        elif constraint_type == "veto_time_slot":
            return f"Forbidden: {description}"
        elif constraint_type == "veto_day":
            return f"Cannot schedule on requested day: {description}"
        else:
            return description
    
    def _constraints_share_scope(self, c1: Dict, c2: Dict) -> bool:
        """Check if two constraints involve the same variables (share scope)"""
        # Simple heuristic: check if they mention the same entities in description
        # In a full implementation, would track actual variable scopes
        desc1 = c1.get("description", "").lower()
        desc2 = c2.get("description", "").lower()
        
        # Check for common keywords
        common_keywords = ["course", "instructor", "room", "time", "week", "day"]
        for keyword in common_keywords:
            if keyword in desc1 and keyword in desc2:
                return True
        
        return False
    
    def _create_graph_visualization_json(self, reasons: List[Dict], edges: List[Dict]) -> Dict:
        """Create JSON structure for graph visualization in UI"""
        return {
            "nodes": [
                {
                    "id": r["id"],
                    "label": r["text"],
                    "type": r["type"],
                    "group": "query" if r["type"].startswith("query") else "minimality" if r["type"] == "minimality" else "constraint"
                }
                for r in reasons
            ],
            "edges": [
                {
                    "source": e["from"],
                    "target": e["to"],
                    "label": e.get("relationship", "")
                }
                for e in edges
            ]
        }
    
    def _format_iis_for_llm(self, iis_constraints: List[Dict], iis_summary: Dict) -> str:
        """Format IIS constraints for LLM prompt"""
        lines = []
        
        lines.append(f"Number of constraints in IIS: {len(iis_constraints)}")
        lines.append(f"Minimality constraint in IIS: {iis_summary.get('minimality_in_iis', False)}")
        lines.append(f"Query constraints in IIS: {iis_summary.get('num_query_constraints_in_iis', 0)}")
        lines.append("\nConstraints:")
        
        for i, constraint in enumerate(iis_constraints, 1):
            constraint_type = constraint.get("type", "unknown")
            description = constraint.get("description", "No description")
            lines.append(f"{i}. [{constraint_type}] {description}")
        
        return "\n".join(lines)
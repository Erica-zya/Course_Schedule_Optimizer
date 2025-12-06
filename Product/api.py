from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import os
import sys

# Add parent directory to path to import config.py from project root
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from pipeline import SchedulingPipeline
from storage import RunStorage
from config import Config
from query_translator import QueryTranslator, validate_query_constraints

# Initialize FastAPI app
app = FastAPI(title="Course Scheduler API", version="1.0.0")

# Enable CORS for Vue.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
# Allow solver type to be configured via environment variable or config
from config import Config
solver_type = Config.SOLVER_TYPE.lower()
if solver_type not in ["julia", "python", "mock"]:
    solver_type = "julia"  # Default fallback

try:
    pipeline = SchedulingPipeline(solver_type=solver_type)
    print(f"✅ Using {solver_type} solver")
except Exception as e:
    print(f"⚠️  {solver_type.capitalize()} solver initialization failed: {e}")
    if solver_type == "julia":
        print("   Falling back to Python solver...")
        solver_type = "python"
        pipeline = SchedulingPipeline(solver_type=solver_type)
        print(f"✅ Using {solver_type} solver (fallback)")
    else:
        raise

storage = RunStorage()


# Request/Response Models
class OptimizationRequest(BaseModel):
    """Request body for optimization endpoint"""
    pass  # Will accept raw JSON matching your input schema


class ExplanationRequest(BaseModel):
    """Request body for explanation endpoint"""
    run_id: str
    question: Optional[str] = None


class ComparisonRequest(BaseModel):
    """Request body for comparison endpoint"""
    run_id1: str
    run_id2: str
    question: Optional[str] = None


# Health check endpoint
@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "message": "Course Scheduler API is running",
        "version": "1.0.0"
    }


# Julia health check endpoint
@app.get("/health/julia")
async def check_julia_health():
    """Check if Julia runtime is healthy"""
    try:
        if hasattr(pipeline, 'solver') and hasattr(pipeline.solver, 'check_julia_health'):
            health = pipeline.solver.check_julia_health()
            if health.get("healthy", False):
                return {
                    "status": "healthy",
                    "julia": health
                }
            else:
                return {
                    "status": "unhealthy",
                    "julia": health,
                    "message": "Julia runtime is not healthy. Server restart may be required."
                }, 503  # Service Unavailable
        else:
            return {
                "status": "unknown",
                "message": "Julia health check not available (solver may not be Julia)"
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "message": "Failed to check Julia health"
        }, 500


# Optimization endpoint
@app.post("/optimize")
async def optimize_schedule(request: Dict[Any, Any]):
    """
    Run optimization on the provided input configuration
    
    Body: Raw JSON matching the scheduling input schema
    Returns: { run_id, status, message }
    """
    try:
        # Validate basic structure
        if not request.get("term_config"):
            raise HTTPException(status_code=400, detail="Missing term_config in input")
        if not request.get("courses"):
            raise HTTPException(status_code=400, detail="Missing courses in input")
        
        # Run optimization with fallback to Python solver if Julia fails
        try:
            run_id, solver_output = pipeline.run_optimization(request, save=True)
        except Exception as e:
            error_str = str(e)
            # If it's a PyJulia access violation, try Python solver as fallback
            if "access violation" in error_str.lower() or "julia" in error_str.lower():
                print(f"⚠️  Julia solver failed with: {error_str}")
                print("   Attempting fallback to Python solver...")
                try:
                    # Create a new pipeline with Python solver
                    fallback_pipeline = SchedulingPipeline(solver_type="python")
                    run_id, solver_output = fallback_pipeline.run_optimization(request, save=True)
                    print("✅ Fallback to Python solver succeeded")
                except Exception as fallback_err:
                    raise HTTPException(
                        status_code=500, 
                        detail=f"Both Julia and Python solvers failed. Julia error: {error_str}. Python error: {str(fallback_err)}"
                    )
            else:
                raise
        
        # Include error details if status is "error"
        response = {
            "run_id": run_id,
            "status": solver_output["status"],
            "message": f"Optimization complete with status: {solver_output['status']}",
            "objective_value": solver_output.get("objective_value"),
            "solve_time": solver_output.get("solve_time_seconds")
        }
        
        # Add error details if there's a real error (not infeasible)
        if solver_output["status"] == "error":
            diagnostics = solver_output.get("diagnostics", {})
            response["error_message"] = diagnostics.get("error", "Unknown error occurred")
            response["error_details"] = diagnostics.get("traceback")
        
        return response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")


# Get all runs
@app.get("/runs")
async def get_runs(limit: int = 20, status: Optional[str] = None):
    """
    Get list of optimization runs
    
    Query params:
    - limit: Max number of runs to return (default 20)
    - status: Filter by status (optional)
    
    Returns: List of run summaries
    """
    try:
        runs = storage.get_run_history(limit=limit)
        
        # Filter by status if provided
        if status:
            runs = [r for r in runs if r["status"] == status]
        
        return runs
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch runs: {str(e)}")


# Get specific run
@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    """
    Get details of a specific run
    
    Returns: Complete run data with input, output, schedule, diagnostics
    """
    try:
        run_data = storage.load_run(run_id)
        
        # Add assignments and conflicts
        if run_data["output"]["status"] == "optimal":
            run_data["assignments"] = storage.get_schedule_for_run(run_id)
            run_data["conflicts"] = storage.get_conflicts_for_run(run_id)
        
        return run_data
    
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load run: {str(e)}")


# Get schedule for a run
@app.get("/runs/{run_id}/schedule")
async def get_schedule(run_id: str):
    """
    Get the schedule (assignments) for a specific run
    
    Returns: List of course assignments
    """
    try:
        assignments = storage.get_schedule_for_run(run_id)
        return {"run_id": run_id, "assignments": assignments}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch schedule: {str(e)}")


# Get conflicts for a run
@app.get("/runs/{run_id}/conflicts")
async def get_conflicts(run_id: str):
    """
    Get student conflicts for a specific run
    
    Returns: List of conflicts with course details
    """
    try:
        conflicts = storage.get_conflicts_for_run(run_id)
        return {"run_id": run_id, "conflicts": conflicts}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch conflicts: {str(e)}")


# Explanation endpoint
@app.post("/explain")
async def explain_schedule(request: ExplanationRequest):
    """
    Get AI explanation for a schedule
    
    Body: { run_id, question? }
    Returns: { explanation }
    """
    try:
        # Load the run
        run_data = storage.load_run(request.run_id)
        
        # Update pipeline's current run
        pipeline.current_run_id = request.run_id
        
        # Build rich context for LLM (same as chat endpoint)
        context = pipeline.explainer._build_input_context(run_data["input"])
        
        # Get schedule assignments if available
        assignments = []
        if run_data["output"]["status"] == "optimal":
            assignments = run_data["output"].get("schedule", {}).get("assignments", [])
        
        # Use explanation agent's formatting methods for consistency
        courses_text = pipeline.explainer._format_courses_for_prompt(context['courses'][:10])
        instructors_text = pipeline.explainer._format_instructors_for_prompt(context['instructors'][:10])
        
        # Format assignments
        assignments_text = ""
        if assignments:
            assignments_text = "\n".join([
                f"- {a.get('course_name', a.get('course_id', '?'))} on {a.get('day', '?')} "
                f"at period {a.get('period_start', '?')} ({a.get('period_length', 1)} periods) "
                f"in {a.get('room_name', a.get('room_id', '?'))}"
                for a in assignments[:20]
            ])
        else:
            assignments_text = "No assignments scheduled (infeasible or error)"
        
        # Format soft constraint summary with proper interpretation
        soft_summary = run_data['output'].get('soft_constraint_summary', {})
        diagnostics = run_data['output'].get('diagnostics', {})
        
        # Check for actual violations
        student_conflicts = diagnostics.get('student_conflicts', [])
        lunch_violations = diagnostics.get('lunch_violations', [])
        
        # Calculate lunch overlaps from assignments
        term_config = run_data['input'].get('term_config', {})
        lunch_start = term_config.get('lunch_start_time', '12:00')
        lunch_end = term_config.get('lunch_end_time', '12:30')
        day_start = term_config.get('day_start_time', '08:00')
        period_length = term_config.get('period_length_minutes', 30)
        
        # Helper to convert period to time
        def period_to_time(period_idx, day_start_str, period_len):
            start_h, start_m = map(int, day_start_str.split(':'))
            start_minutes = start_h * 60 + start_m
            period_start_minutes = start_minutes + (period_idx * period_len)
            period_end_minutes = period_start_minutes + period_len
            return period_start_minutes, period_end_minutes
        
        # Check which assignments overlap with lunch
        lunch_overlapping_assignments = []
        lunch_start_h, lunch_start_m = map(int, lunch_start.split(':'))
        lunch_end_h, lunch_end_m = map(int, lunch_end.split(':'))
        lunch_start_minutes = lunch_start_h * 60 + lunch_start_m
        lunch_end_minutes = lunch_end_h * 60 + lunch_end_m
        
        for a in assignments:
            period_start = a.get('period_start', 0)
            period_length_assignment = a.get('period_length', 1)
            period_start_min, period_end_min = period_to_time(period_start, day_start, period_length)
            period_end_min = period_start_min + (period_length_assignment * period_length)
            
            # Check if overlaps with lunch
            if max(period_start_min, lunch_start_minutes) < min(period_end_min, lunch_end_minutes):
                lunch_overlapping_assignments.append(a)
        
        # Build human-readable soft constraint summary
        s1_val = soft_summary.get('S1_student_conflicts', {}).get('weighted_penalty', 0)
        s2_val = soft_summary.get('S2_instructor_compactness', {}).get('weighted_penalty', 0)
        s3_val = soft_summary.get('S3_preferred_time_slots', {}).get('weighted_penalty', 0)
        
        # Determine S3 explanation - be explicit about lunch overlaps
        if s3_val == 0:
            s3_explanation = "NO courses overlap with lunch hours (12:00-12:30) - no lunch penalty"
        elif len(lunch_overlapping_assignments) > 0:
            # Build detailed list of courses with their times
            courses_details = []
            for a in lunch_overlapping_assignments[:5]:
                period_start = a.get('period_start', 0)
                period_len = a.get('period_length', 1)
                period_start_min, _ = period_to_time(period_start, day_start, period_length)
                period_end_min = period_start_min + (period_len * period_length)
                
                start_h = period_start_min // 60
                start_m = period_start_min % 60
                end_h = period_end_min // 60
                end_m = period_end_min % 60
                
                time_str = f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}"
                course_name = a.get('course_name', a.get('course_id', '?'))
                courses_details.append(f"{course_name} ({time_str})")
            
            courses_list = ", ".join(courses_details)
            if len(lunch_overlapping_assignments) > 5:
                courses_list += f" and {len(lunch_overlapping_assignments) - 5} more"
            s3_explanation = f"{len(lunch_overlapping_assignments)} course(s) ARE SCHEDULED DURING/OVERLAPPING lunch hours (12:00-12:30): {courses_list}. This causes the {s3_val:.1f} penalty."
        else:
            # S3 > 0 but no detected overlaps - might be from instructor preferences or evening slots
            s3_explanation = f"Penalty of {s3_val:.1f} from time slot preferences (may be from instructor lunch preferences or evening scheduling)"
        
        # Pre-compute interpretation strings to avoid nested f-strings
        s1_interpretation = 'NO student conflicts occurred' if s1_val == 0 else f'{len(student_conflicts)} students have schedule conflicts'
        
        if abs(s2_val) < 0.1:
            s2_interpretation = 'Instructor preferences were honored (neutral impact)'
        elif s2_val > 0:
            s2_interpretation = 'Some instructors did not get preferred teaching patterns'
        else:
            s2_interpretation = 'Instructors got preferred patterns (reward)'
        
        soft_constraints_text = f"""SOFT CONSTRAINT BREAKDOWN:
- S1 (Student Conflicts): {s1_val:.1f} penalty
  → Actual conflicts: {len(student_conflicts)} students with overlapping courses
  → Interpretation: {s1_interpretation}

- S2 (Instructor Back-to-Back Preferences): {s2_val:.1f} penalty
  → Interpretation: {s2_interpretation}

- S3 (Lunch/Evening Time Slots): {s3_val:.1f} penalty
  → {s3_explanation}
  → IMPORTANT: If s3_val > 0 but no courses overlap lunch, the penalty comes from instructor lunch preferences (instructors who don't allow lunch teaching)

TOTAL OBJECTIVE: {run_data['output'].get('objective_value', 'N/A')} = S1 ({s1_val:.1f}) + S2 ({s2_val:.1f}) + S3 ({s3_val:.1f})"""
        
        # Determine the question to ask
        if request.question:
            user_question = request.question
        else:
            user_question = "Please provide a comprehensive explanation of this schedule, including the objective value breakdown and what each component means."
        
        # Pre-compute strings for the prompt to avoid nested f-strings
        s1_summary = 'NO conflicts' if s1_val == 0 else f'{len(student_conflicts)} conflicts occurred'
        s2_summary = 'REWARD for preferred back-to-back patterns' if s2_val < 0 else 'Neutral or penalty for patterns'
        
        # Build comprehensive prompt for LLM
        prompt = f"""You are an expert optimization assistant explaining a Mixed-Integer Linear Programming (MILP) solution for course scheduling.

OPTIMIZATION RESULT:
- Status: {run_data['output']['status']}
- Objective Value: {run_data['output'].get('objective_value', 'N/A')}

PROBLEM SIZE:
- {len(context['courses'])} courses, {len(context['instructors'])} instructors, {len(context['students'])} students, {len(context['rooms'])} rooms

COURSES:
{courses_text}

INSTRUCTORS:
{instructors_text}

SCHEDULE ASSIGNMENTS:
{assignments_text}

{soft_constraints_text}

USER'S QUESTION: {user_question}

INSTRUCTIONS - Provide a clear, CONCISE explanation in PLAIN PARAGRAPHS (no bullet points, no numbered lists):

Write 3 short paragraphs (2-3 sentences each):

Paragraph 1 - HARD CONSTRAINTS: State that all hard constraints were satisfied (no instructor/room conflicts, all courses scheduled, capacity/availability respected). Do NOT list specific course assignments or times - the user can see those in the schedule.

Paragraph 2 - SOFT CONSTRAINTS & OBJECTIVE: Explain the three components and their sum:
- S1 (Student Conflicts): {s1_val:.1f} - {s1_summary}
- S2 (Instructor Preferences): {s2_val:.1f} - {s2_summary}  
- S3 (Time Slots): {s3_val:.1f} - {s3_explanation}
Total = {run_data['output'].get('objective_value', 'N/A')}. Explain if this is good (negative = reward) or bad (positive = penalty).

Paragraph 3 - WHY OPTIMAL: Briefly state this is the best solution satisfying all hard constraints while optimizing soft constraints. Mention key trade-offs only if they exist.

CRITICAL RULES:
- Do NOT repeat specific course names, times, rooms, or assignments - user can see the schedule
- Do NOT explain formatting details or output display
- Focus on the OPTIMIZATION RESULT, not schedule details
- S1 = 0.0 means NO conflicts; S2 < 0 means REWARD; S3 = 0.0 means no lunch violations
- Be concise: 6-9 sentences total
- Write in flowing paragraphs, NOT bullet points"""
        
        # Generate explanation using LLM
        response = pipeline.explainer.model.generate_content(prompt)
        explanation = response.text
        
        return {
            "run_id": request.run_id,
            "explanation": explanation
        }
    
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run {request.run_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Explanation failed: {str(e)}")


# Interactive Chat endpoint
@app.post("/chat")
async def chat_with_ai(request: Dict[Any, Any]):
    """
    Interactive chat with AI assistant about the schedule
    Sends user query directly to LLM with full schedule context
    
    Body: {
        run_id: str,
        message: str,
        conversation_history: list (optional)  # Previous messages for context
    }
    
    Returns: {
        response: str,
        conversation_id: str (optional)
    }
    """
    try:
        run_id = request.get("run_id")
        message = request.get("message", "").strip()
        conversation_history = request.get("conversation_history", [])
        
        if not run_id:
            raise HTTPException(status_code=400, detail="run_id is required")
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        
        # Load the run
        run_data = storage.load_run(run_id)
        
        # Build rich context for LLM
        context = pipeline.explainer._build_input_context(run_data["input"])
        
        # Get schedule assignments if available
        assignments = []
        if run_data["output"]["status"] == "optimal":
            assignments = run_data["output"].get("schedule", {}).get("assignments", [])
        
        # Build conversation context
        conversation_context = ""
        if conversation_history:
            conversation_context = "\n\nPrevious conversation:\n"
            for msg in conversation_history[-5:]:  # Last 5 messages for context
                role = msg.get("role", "user")
                content = msg.get("content", "")
                conversation_context += f"{role.upper()}: {content}\n"
        
        # Use explanation agent's formatting methods for consistency
        courses_text = pipeline.explainer._format_courses_for_prompt(context['courses'][:10])
        instructors_text = pipeline.explainer._format_instructors_for_prompt(context['instructors'][:10])
        
        # Format assignments
        assignments_text = ""
        if assignments:
            assignments_text = "\n".join([
                f"- {a.get('course_name', a.get('course_id', '?'))} on {a.get('day', '?')} "
                f"at period {a.get('period_start', '?')} ({a.get('period_length', 1)} periods) "
                f"in {a.get('room_name', a.get('room_id', '?'))}"
                for a in assignments[:20]
            ])
        else:
            assignments_text = "No assignments scheduled (infeasible or error)"
        
        # Format soft constraint summary with proper interpretation
        soft_summary = run_data['output'].get('soft_constraint_summary', {})
        diagnostics = run_data['output'].get('diagnostics', {})
        
        # Check for actual violations
        student_conflicts = diagnostics.get('student_conflicts', [])
        lunch_violations = diagnostics.get('lunch_violations', [])
        
        # Calculate lunch overlaps from assignments
        term_config = run_data['input'].get('term_config', {})
        lunch_start = term_config.get('lunch_start_time', '12:00')
        lunch_end = term_config.get('lunch_end_time', '12:30')
        day_start = term_config.get('day_start_time', '08:00')
        period_length = term_config.get('period_length_minutes', 30)
        
        # Helper to convert period to time
        def period_to_time(period_idx, day_start_str, period_len):
            start_h, start_m = map(int, day_start_str.split(':'))
            start_minutes = start_h * 60 + start_m
            period_start_minutes = start_minutes + (period_idx * period_len)
            period_end_minutes = period_start_minutes + period_len
            return period_start_minutes, period_end_minutes
        
        # Check which assignments overlap with lunch
        lunch_overlapping_assignments = []
        lunch_start_h, lunch_start_m = map(int, lunch_start.split(':'))
        lunch_end_h, lunch_end_m = map(int, lunch_end.split(':'))
        lunch_start_minutes = lunch_start_h * 60 + lunch_start_m
        lunch_end_minutes = lunch_end_h * 60 + lunch_end_m
        
        for a in assignments:
            period_start = a.get('period_start', 0)
            period_length_assignment = a.get('period_length', 1)
            period_start_min, period_end_min = period_to_time(period_start, day_start, period_length)
            period_end_min = period_start_min + (period_length_assignment * period_length)
            
            # Check if overlaps with lunch
            if max(period_start_min, lunch_start_minutes) < min(period_end_min, lunch_end_minutes):
                lunch_overlapping_assignments.append(a)
        
        # Build human-readable soft constraint summary
        s1_val = soft_summary.get('S1_student_conflicts', {}).get('weighted_penalty', 0)
        s2_val = soft_summary.get('S2_instructor_compactness', {}).get('weighted_penalty', 0)
        s3_val = soft_summary.get('S3_preferred_time_slots', {}).get('weighted_penalty', 0)
        
        # Determine S3 explanation - be explicit about lunch overlaps
        if s3_val == 0:
            s3_explanation = "NO courses overlap with lunch hours (12:00-12:30) - no lunch penalty"
        elif len(lunch_overlapping_assignments) > 0:
            # Build detailed list of courses with their times
            courses_details = []
            for a in lunch_overlapping_assignments[:5]:
                period_start = a.get('period_start', 0)
                period_len = a.get('period_length', 1)
                period_start_min, _ = period_to_time(period_start, day_start, period_length)
                period_end_min = period_start_min + (period_len * period_length)
                
                start_h = period_start_min // 60
                start_m = period_start_min % 60
                end_h = period_end_min // 60
                end_m = period_end_min % 60
                
                time_str = f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}"
                course_name = a.get('course_name', a.get('course_id', '?'))
                courses_details.append(f"{course_name} ({time_str})")
            
            courses_list = ", ".join(courses_details)
            if len(lunch_overlapping_assignments) > 5:
                courses_list += f" and {len(lunch_overlapping_assignments) - 5} more"
            s3_explanation = f"{len(lunch_overlapping_assignments)} course(s) ARE SCHEDULED DURING/OVERLAPPING lunch hours (12:00-12:30): {courses_list}. This causes the {s3_val:.1f} penalty."
        else:
            # S3 > 0 but no detected overlaps - might be from instructor preferences or evening slots
            s3_explanation = f"Penalty of {s3_val:.1f} from time slot preferences (may be from instructor lunch preferences or evening scheduling)"
        
        # Pre-compute interpretation strings to avoid nested f-strings
        s1_interpretation = 'NO student conflicts occurred' if s1_val == 0 else f'{len(student_conflicts)} students have schedule conflicts'
        
        if abs(s2_val) < 0.1:
            s2_interpretation = 'Instructor preferences were honored (neutral impact)'
        elif s2_val > 0:
            s2_interpretation = 'Some instructors did not get preferred teaching patterns'
        else:
            s2_interpretation = 'Instructors got preferred patterns (reward)'
        
        # Build soft constraints text using string formatting
        total_obj = run_data['output'].get('objective_value', 'N/A')
        soft_constraints_text = (
            f"SOFT CONSTRAINT BREAKDOWN:\n"
            f"- S1 (Student Conflicts): {s1_val:.1f} penalty\n"
            f"  → Actual conflicts: {len(student_conflicts)} students with overlapping courses\n"
            f"  → Interpretation: {s1_interpretation}\n"
            f"\n"
            f"- S2 (Instructor Back-to-Back Preferences): {s2_val:.1f} penalty\n"
            f"  → Interpretation: {s2_interpretation}\n"
            f"\n"
            f"- S3 (Lunch/Evening Time Slots): {s3_val:.1f} penalty\n"
            f"  → {s3_explanation}\n"
            f"  → IMPORTANT: If s3_val > 0 but no courses overlap lunch, the penalty comes from instructor lunch preferences (instructors who don't allow lunch teaching)\n"
            f"\n"
            f"TOTAL OBJECTIVE: {total_obj} = S1 ({s1_val:.1f}) + S2 ({s2_val:.1f}) + S3 ({s3_val:.1f})"
        )
        
        # Build comprehensive prompt
        prompt = f"""You are an AI assistant helping with course scheduling optimization.

CURRENT SCHEDULE CONTEXT:
- Status: {run_data['output']['status']}
- Objective Value: {run_data['output'].get('objective_value', 'N/A')}
- Number of courses: {len(context['courses'])}
- Number of instructors: {len(context['instructors'])}
- Number of students: {len(context['students'])}
- Number of classrooms: {len(context['rooms'])}

COURSES:
{courses_text}

INSTRUCTORS:
{instructors_text}

SCHEDULE ASSIGNMENTS:
{assignments_text}

{soft_constraints_text}

{conversation_context}

USER QUESTION: {message}

CRITICAL RULES FOR INTERPRETING SOFT CONSTRAINTS:
- S1 penalty = 0.0 means NO student conflicts occurred (all students have conflict-free schedules)
- S2 penalty = 0.0 means instructor back-to-back preferences were neutral or honored
- S3 penalty interpretation (Lunch/Evening Time Slots):
  * If S3 = 0.0: NO courses overlap with lunch hours (12:00-12:30) - no lunch penalty
  * If S3 > 0.0 AND lunch_overlapping_assignments > 0: 
    - COURSES ARE SCHEDULED DURING/OVERLAPPING LUNCH HOURS (12:00-12:30)
    - This is WHY there is a penalty - the course times overlap with the lunch period
    - State clearly: "Course X is scheduled from Y:YY to Z:ZZ, which overlaps with lunch (12:00-12:30), causing the penalty"
  * If S3 > 0.0 BUT lunch_overlapping_assignments = 0: The penalty comes from instructor lunch preferences or evening slots
- IMPORTANT: If a course runs 11:00-12:30, it OVERLAPS lunch (12:00-12:30) and WILL get a penalty
- NEVER say "no courses during lunch" if S3 > 0.0 and lunch_overlapping_assignments > 0
- ALWAYS check the actual course times - if a course spans 12:00-12:30, it overlaps lunch
- Be explicit: "The course is scheduled during lunch hours, which causes the S3 penalty"

INSTRUCTIONS:
- Answer the user question directly and conversationally
- Use specific course names, instructor names, and numbers from the context
- If asked about why something happened, reference the constraints and objective function
- Be accurate: if a penalty is 0 and violations are 0, say there are NO violations
- Be concise but helpful (2-4 sentences typically)
- If you do not have enough information, say so and suggest what might help
- Format your response naturally - no bullet points unless the user asks for a list

Your response:"""
        
        # Send to LLM
        response = pipeline.explainer.model.generate_content(prompt)
        
        ai_response = response.text if response and response.text else "I apologize, but I couldn't generate a response. Please try rephrasing your question."
        
        return {
            "run_id": run_id,
            "response": ai_response,
            "message": message
        }
    
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


# Comparison endpoint
@app.post("/compare")
async def compare_schedules(request: ComparisonRequest):
    """
    Compare two schedules
    
    Body: { run_id1, run_id2, question? }
    Returns: { comparison_text, changed_assignments }
    """
    try:
        # Get comparison data
        comparison_data = storage.compare_runs(request.run_id1, request.run_id2)
        
        # Generate explanation
        old_run = storage.load_run(request.run_id1)
        new_run = storage.load_run(request.run_id2)
        
        explanation = pipeline.explainer.compare_schedules(
            old_run=old_run,
            new_run=new_run,
            question=request.question
        )
        
        return {
            "run_id1": request.run_id1,
            "run_id2": request.run_id2,
            "comparison_text": explanation,
            "comparison_data": comparison_data
        }
    
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")


# What-If Analysis endpoint
@app.post("/what-if")
async def what_if_analysis(request: Dict[Any, Any]):
    """
    Run counterfactual what-if analysis on a schedule
    Based on X-MILP paper: User-Desired Satisfiability Problem (UDSP)
    
    Body: {
        run_id: str,              # ID of original run to compare against
        query_type: str,          # Type of query (e.g., "enforce_time_slot")
        query_params: dict,       # Parameters for the query
        question: str (optional)  # Natural language description
    }
    
    Returns: {
        feasible: bool,
        explanation: str,
        alternative_schedule: dict (if feasible),
        iis: list (if infeasible),
        graph_of_reasons: dict (if infeasible)
    }
    """
    try:
        run_id = request.get("run_id")
        query_type = request.get("query_type")
        query_params = request.get("query_params", {})
        question = request.get("question", "")
        
        if not run_id:
            raise HTTPException(status_code=400, detail="run_id is required")
        if not query_type:
            raise HTTPException(status_code=400, detail="query_type is required")
        
        # Load original run
        original_run = storage.load_run(run_id)
        
        if original_run["output"]["status"] != "optimal":
            raise HTTPException(
                status_code=400,
                detail="Can only run what-if analysis on optimal schedules"
            )
        
        original_objective = original_run["output"]["objective_value"]
        
        # Translate query to constraints
        translator = QueryTranslator()
        
        # Add current schedule to params for swap queries
        if query_type == "swap_time_slots":
            query_params["current_schedule"] = original_run["output"]["schedule"]
        
        query_constraints = translator.parse_structured_query(
            query_type,
            query_params,
            original_run["input"]
        )
        
        # Validate constraints
        is_valid, errors = validate_query_constraints(
            query_constraints,
            original_run["input"]
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid query constraints: {'; '.join(errors)}"
            )
        
        # Generate query description
        if not question:
            if query_constraints:
                question = query_constraints[0].to_natural_language()
                if len(query_constraints) > 1:
                    question += f" (and {len(query_constraints) - 1} more constraints)"
            else:
                question = "What-if scenario"
        
        # Solve UDSP
        query_constraints_dicts = [qc.to_dict() for qc in query_constraints]
        
        what_if_result = pipeline.solver.solve_what_if(
            original_run["input"],
            query_constraints_dicts,
            original_objective
        )
        
        # Build input context for explanation
        input_context = pipeline._summarize_input(original_run["input"])
        
        # Generate explanation
        explanation = pipeline.explainer.explain_what_if_result(
            what_if_result,
            question,
            original_run["input"]
        )
        
        # Format response
        response = {
            "run_id": run_id,
            "query_description": question,
            "query_type": query_type,
            "query_constraints": [qc.to_dict() for qc in query_constraints],
            "feasible": what_if_result.get("query_feasible", False),
            "status": what_if_result.get("status"),
            "explanation": explanation,
            "original_objective": original_objective,
            "solve_time": what_if_result.get("solve_time_seconds", 0)
        }
        
        if what_if_result.get("query_feasible"):
            # Include alternative schedule
            response["alternative_schedule"] = what_if_result.get("alternative_schedule", {})
            response["alternative_objective"] = what_if_result.get("alternative_objective")
            response["objective_difference"] = what_if_result.get("objective_difference")
            response["soft_constraints"] = what_if_result.get("alternative_soft_constraints", {})
        else:
            # Include IIS and graph of reasons
            response["iis"] = what_if_result.get("iis", [])
            response["iis_summary"] = what_if_result.get("iis_summary", {})
            
            # Build graph of reasons for visualization
            if what_if_result.get("iis"):
                graph = pipeline.explainer.build_graph_of_reasons(
                    what_if_result["iis"],
                    question,
                    original_run["input"]
                )
                response["graph_of_reasons"] = graph
        
        return response
    
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"What-if analysis failed: {str(e)}")


# Statistics endpoint
@app.get("/statistics")
async def get_statistics():
    """
    Get overall system statistics
    
    Returns: Aggregated statistics across all runs
    """
    try:
        stats = storage.get_run_statistics()
        return stats
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")


# Get entities (for UI dropdowns)
@app.get("/entities/courses")
async def get_courses():
    """Get all courses"""
    try:
        return storage.get_courses()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/entities/instructors")
async def get_instructors():
    """Get all instructors"""
    try:
        return storage.get_instructors()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/entities/classrooms")
async def get_classrooms():
    """Get all classrooms"""
    try:
        return storage.get_classrooms()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/entities/students")
async def get_students():
    """Get all students with enrollments"""
    try:
        return storage.get_students()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Delete run endpoint
@app.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """
    Delete a specific run
    
    Returns: Success message
    """
    try:
        storage.delete_run(run_id)
        return {"message": f"Run {run_id} deleted successfully"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete run: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*70)
    print("🚀 Starting Course Scheduler API Server")
    print("="*70)
    print("\n📍 API will be available at: http://localhost:8000")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("\n💡 To start the server, run:")
    print("   uvicorn api:app --reload")
    print("\n" + "="*70 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
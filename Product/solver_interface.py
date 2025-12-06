from typing import Dict, Any, List
import json
import os
import sys

# Add parent directory to path to import config.py from project root
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from config import Config


class SolverInterface:
    """
    Interface to the optimization solver
    Uses Julia/JuMP solver exclusively
    """
    
    def __init__(self, use_julia_solver: bool = True):
        """
        Initialize the solver interface
        
        Args:
            use_julia_solver: If True, use Julia/JuMP solver (default), else use mock
        """
        self.use_julia_solver = use_julia_solver
        
        if use_julia_solver:
            print("🔧 Initializing Julia solver...")
            self._setup_julia()
        else:
            print("⚠️ Mock solver mode (testing only)")
    
    def _setup_julia(self, force_reinit: bool = False):
        """Initialize Julia runtime and load solver module
        
        Args:
            force_reinit: If True, attempt to reinitialize Julia even if it seems corrupted
        """
        try:
            from julia import Main
            import os
            import sys
            
            # Test if Julia is accessible before attempting setup
            if not force_reinit:
                try:
                    _ = Main.eval("1 + 1")
                except (OSError, AttributeError, RuntimeError) as test_err:
                    error_str = str(test_err).lower()
                    if "access violation" in error_str or "corrupted" in error_str:
                        raise RuntimeError(
                            f"Julia runtime is corrupted (access violation detected). "
                            f"Please restart the Python server to reinitialize Julia. "
                            f"Error: {test_err}"
                        )
            
            # Setup Gurobi license before loading Julia
            self._setup_gurobi_license()
            
            self.julia = Main
            
            # Get the project root directory (go up one level from Product/)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)  # Go up from Product/ to project root
            
            # Load the Julia solver module (folder is now Julia_Solver with capital letters)
            solver_path = os.path.join(project_root, 'Julia_Solver', 'course_scheduler.jl')
            
            # Normalize path for Julia (use forward slashes)
            solver_path_normalized = solver_path.replace('\\', '/')
            
            if not os.path.exists(solver_path):
                raise FileNotFoundError(
                    f"Julia solver not found at {solver_path}\n"
                    f"Current working directory: {os.getcwd()}\n"
                    f"Project root: {project_root}"
                )
            
            # Load the Julia file
            try:
                # Remove all functions from the solver module to force fresh load
                self.julia.eval('''
                    if isdefined(Main, :solve_scheduling_problem)
                        rm!(Main, :solve_scheduling_problem)
                    end
                    if isdefined(Main, :format_optimal_solution)
                        rm!(Main, :format_optimal_solution)
                    end
                    if isdefined(Main, :format_output)
                        rm!(Main, :format_output)
                    end
                ''')
            except (OSError, RuntimeError) as e:
                error_str = str(e).lower()
                if "access violation" in error_str:
                    raise RuntimeError(
                        f"Julia runtime is corrupted. Cannot reload solver module. "
                        f"Please restart the Python server. Error: {e}"
                    )
                pass  # Ignore other errors if functions don't exist
            
            # Load/reload the file - this will recompile the module
            try:
                self.julia.eval(f'include(raw"{solver_path_normalized}")')
                print(f"✅ Julia solver loaded successfully from {solver_path}")
            except (OSError, RuntimeError) as e:
                error_str = str(e).lower()
                if "access violation" in error_str:
                    raise RuntimeError(
                        f"Julia runtime is corrupted. Cannot load solver module. "
                        f"Please restart the Python server. Error: {e}"
                    )
                raise
            
        except ImportError as e:
            raise RuntimeError(
                "PyJulia not installed. Run:\n"
                "  pip install julia\n"
                "  python -c 'import julia; julia.install()'\n"
                f"Error: {e}"
            )
        except RuntimeError:
            # Re-raise RuntimeErrors (including our corruption errors)
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "access violation" in error_str:
                raise RuntimeError(
                    f"Julia runtime is corrupted. Please restart the Python server. Error: {e}"
                )
            raise RuntimeError(f"Failed to load Julia solver: {e}")
    
    def _setup_gurobi_license(self):
        """Setup Gurobi WLS license from gurobi.lic file"""
        import os
        from pathlib import Path
        
        # Get project root directory (go up one level from Product/)
        current_dir = Path(__file__).parent
        project_root = current_dir.parent  # Go up from Product/ to project root
        license_file = project_root / 'Julia_Solver' / 'gurobi.lic'
        
        if license_file.exists():
            # Read license file
            with open(license_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            # Set environment variable for Gurobi
                            # Gurobi uses both standard names and GRB_ prefix
                            os.environ[key] = value
                            os.environ[f'GRB_{key}'] = value  # Gurobi.jl may use this
                            print(f"🔑 Set Gurobi license: {key}")
            
            # Also set the license file path for Gurobi to find
            os.environ['GRB_LICENSE_FILE'] = str(license_file.absolute())
            print(f"✅ Gurobi license configured from {license_file}")
        else:
            # Fallback to config.py values
            from config import Config
            os.environ['WLSACCESSID'] = Config.GUROBI_WLS_LICENSE_ID
            os.environ['GRB_WLSACCESSID'] = Config.GUROBI_WLS_LICENSE_ID
            os.environ['LICENSEID'] = Config.GUROBI_WLS_LICENSE_ID
            os.environ['GRB_LICENSEID'] = Config.GUROBI_WLS_LICENSE_ID
            print("⚠️ gurobi.lic not found, using config.py values")
    
    def check_julia_health(self) -> Dict[str, Any]:
        """
        Check if Julia runtime is healthy and accessible
        
        Returns:
            Dict with 'healthy' (bool) and 'error' (str if unhealthy)
        """
        if not self.use_julia_solver:
            return {"healthy": True, "message": "Mock solver mode"}
        
        try:
            # Simple test to see if Julia is accessible
            result = self.julia.eval("1 + 1")
            if result == 2:
                return {"healthy": True, "message": "Julia runtime is healthy"}
            else:
                return {"healthy": False, "error": f"Unexpected Julia response: {result}"}
        except (OSError, RuntimeError) as e:
            error_str = str(e).lower()
            if "access violation" in error_str:
                return {
                    "healthy": False,
                    "error": "Julia runtime is corrupted (access violation)",
                    "requires_restart": True
                }
            return {"healthy": False, "error": f"Julia runtime error: {e}"}
        except Exception as e:
            return {"healthy": False, "error": f"Unexpected error checking Julia: {e}"}
    
    def solve(self, input_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the optimization solver
        
        Args:
            input_json: Scheduling input following the schema
        
        Returns:
            Solver output with schedule and diagnostics
        """
        if self.use_julia_solver:
            return self._solve_julia(input_json)
        else:
            print("Using mock solver (no actual optimization)...")
            return self._mock_solve(input_json)
    
    def _solve_julia(self, input_json: Dict[str, Any]) -> Dict[str, Any]:
        """Call Julia solver via PyJulia"""
        try:
            # Ensure Gurobi license is set before each solve
            self._setup_gurobi_license()
            
            print("🔧 Calling Julia solver...")
            
            # Validate input before calling Julia
            if not isinstance(input_json, dict):
                raise ValueError("Input must be a dictionary")
            if "courses" not in input_json or len(input_json.get("courses", [])) == 0:
                raise ValueError("Input must contain at least one course")
            if "instructors" not in input_json or len(input_json.get("instructors", [])) == 0:
                raise ValueError("Input must contain at least one instructor")
            
            # Ensure input has all required fields
            if "conflict_weights" not in input_json:
                input_json["conflict_weights"] = {
                    "global_student_conflict_weight": 50.0,
                    "instructor_compactness_weight": 1.0,
                    "preferred_time_slots_weight": 1.0
                }
            
            # Try to call Julia solver with retry logic
            max_retries = 2
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    # Test if Julia is still accessible (only on retry)
                    if attempt > 0:
                        print(f"   Retry attempt {attempt + 1}/{max_retries}...")
                        try:
                            _ = self.julia.eval("1 + 1")
                        except (OSError, RuntimeError) as test_err:
                            error_str = str(test_err).lower()
                            if "access violation" in error_str:
                                raise RuntimeError(
                                    f"Julia runtime is corrupted (access violation). "
                                    f"Cannot continue. Please restart the Python server. "
                                    f"Error: {test_err}"
                                )
                            print(f"⚠️ Julia runtime test failed: {test_err}")
                            print("   Attempting to reload Julia module...")
                            try:
                                self._setup_julia(force_reinit=True)
                            except RuntimeError as reload_err:
                                # Re-raise corruption errors
                                raise
                            except Exception as reload_err:
                                print(f"❌ Failed to reload Julia: {reload_err}")
                                raise RuntimeError(f"Julia runtime corrupted: {reload_err}")
                    
                    # Convert Python dict to Julia-compatible format
                    # PyJulia handles most of the conversion automatically
                    result = self.julia.solve_scheduling_problem(input_json)
                    break  # Success, exit retry loop
                    
                except (OSError, MemoryError) as e:
                    # Access violation or memory errors - these are PyJulia bridge issues
                    error_str = str(e).lower()
                    if "access violation" in error_str:
                        # Don't retry on access violations - runtime is corrupted
                        raise RuntimeError(
                            f"Julia runtime is corrupted (access violation detected). "
                            f"This is a low-level memory issue with the Python-Julia bridge. "
                            f"Please restart the Python server to reinitialize Julia. "
                            f"Error: {e}"
                        ) from e
                    
                    last_error = e
                    if attempt < max_retries - 1:
                        print(f"⚠️ PyJulia bridge error (attempt {attempt + 1}): {e}")
                        print("   Will retry after brief delay...")
                        import time
                        time.sleep(0.5)  # Brief delay before retry
                    else:
                        # Last attempt failed
                        raise RuntimeError(
                            f"PyJulia bridge error after {max_retries} attempts: {e}. "
                            f"This is a low-level memory/access issue with the Python-Julia bridge. "
                            f"Try restarting the server."
                        ) from e
                except RuntimeError:
                    # Re-raise RuntimeErrors (including corruption errors)
                    raise
                except Exception as e:
                    # Other errors - don't retry
                    raise
            
            # Convert Julia dict back to Python dict
            python_result = self._julia_to_python(result)
            
            # Post-process assignments: group consecutive periods into single assignments
            if python_result.get('status') == 'optimal':
                assignments = python_result.get("schedule", {}).get("assignments", [])
                if assignments:
                    assignments = self._group_consecutive_periods(assignments, input_json)
                    python_result["schedule"]["assignments"] = assignments
            
            status = python_result.get('status', 'unknown')
            print(f"✅ Julia solver returned: {status}")
            
            if status == "optimal":
                num_assignments = len(python_result.get("schedule", {}).get("assignments", []))
                obj_val = python_result.get("objective_value", "N/A")
                print(f"   Assignments: {num_assignments}, Objective: {obj_val}")
            
            return python_result
            
        except (OSError, RuntimeError) as e:
            # Access violation or memory errors from PyJulia
            import traceback
            error_msg = str(e)
            error_str = error_msg.lower()
            
            is_corruption = "access violation" in error_str or "corrupted" in error_str
            
            if is_corruption:
                print(f"❌ Julia runtime corrupted (access violation): {error_msg}")
                print(f"   The Julia runtime has become corrupted and cannot be recovered.")
                print(f"   Traceback: {traceback.format_exc()}")
                print(f"💡 REQUIRED ACTION: Restart the Python server to reinitialize Julia")
            else:
                print(f"❌ Julia runtime error (memory issue): {error_msg}")
                print(f"   This is a PyJulia bridge issue, not a solver logic error.")
                print(f"   Traceback: {traceback.format_exc()}")
                print(f"💡 Suggestion: Try restarting the Python server")
            
            return {
                "status": "error",
                "objective_value": None,
                "solve_time_seconds": 0.0,
                "hard_constraints_ok": False,
                "violated_hard_constraints": [],
                "soft_constraint_summary": {},
                "schedule": {"assignments": []},
                "diagnostics": {
                    "error": f"Julia runtime error: {error_msg}. " + 
                            ("The Julia runtime is corrupted and requires a server restart. " if is_corruption else 
                             "This is a PyJulia bridge issue. Try restarting the server."),
                    "traceback": traceback.format_exc(),
                    "solver": "Julia/JuMP (Python bridge)",
                    "error_type": "Julia runtime corruption" if is_corruption else "PyJulia bridge error",
                    "requires_restart": is_corruption
                },
                "metadata": {
                    "solver": "Julia/JuMP",
                    "error_location": "Python-Julia bridge (PyJulia)",
                    "corruption_detected": is_corruption
                }
            }
        except Exception as e:
            import traceback
            error_msg = str(e)
            print(f"❌ Julia solver error: {error_msg}")
            print(f"   Traceback: {traceback.format_exc()}")
            
            return {
                "status": "error",
                "objective_value": None,
                "solve_time_seconds": 0.0,
                "hard_constraints_ok": False,
                "violated_hard_constraints": [],
                "soft_constraint_summary": {},
                "schedule": {"assignments": []},
                "diagnostics": {
                    "error": error_msg,
                    "traceback": traceback.format_exc(),
                    "solver": "Julia/JuMP (Python bridge)"
                },
                "metadata": {
                    "solver": "Julia/JuMP",
                    "error_location": "Python-Julia bridge"
                }
            }
    
    def _julia_to_python(self, julia_obj):
        """
        Convert Julia object to Python dict recursively
        
        PyJulia handles most conversions, but we ensure proper dict structure
        """
        # Handle None/null values
        if julia_obj is None:
            return None
        
        # Handle Julia Dict (PyJulia converts these to Python dicts)
        if isinstance(julia_obj, dict):
            return {str(k): self._julia_to_python(v) for k, v in julia_obj.items()}
        
        # Handle lists/arrays
        if isinstance(julia_obj, (list, tuple)):
            return [self._julia_to_python(item) for item in julia_obj]
        
        # Handle numpy arrays (PyJulia sometimes converts to numpy)
        try:
            import numpy as np
            if isinstance(julia_obj, np.ndarray):
                return julia_obj.tolist()
        except ImportError:
            pass
        
        # Handle objects with __dict__
        if hasattr(julia_obj, '__dict__'):
            return {k: self._julia_to_python(v) for k, v in julia_obj.__dict__.items()}
        
        # Handle Julia types that need conversion
        # PyJulia should handle most of these, but we catch edge cases
        if hasattr(julia_obj, 'value'):
            # Some Julia wrapped types
            return self._julia_to_python(julia_obj.value)
        
        # Primitive types (int, float, str, bool) - return as-is
        return julia_obj
    
    def _group_consecutive_periods(self, assignments: list, input_json: Dict[str, Any]) -> list:
        """
        Group consecutive periods for the same course into single assignments.
        
        Julia solver may output assignments with period_length > 1 (correct) or period_length=1 (needs grouping).
        This function handles both cases and ensures proper grouping.
        """
        if not assignments:
            return assignments
        
        # Check if assignments already have correct period_length (> 1)
        # If so, we just need to deduplicate, not regroup
        has_multi_period = any(a.get("period_length", 1) > 1 for a in assignments)
        
        if has_multi_period:
            # Julia solver already output correct period_length, just deduplicate
            # Group by (course_id, week, day, room_id, period_start) to remove exact duplicates
            seen = {}
            deduplicated = []
            for assignment in assignments:
                key = (
                    assignment["course_id"],
                    assignment["week"],
                    assignment["day"],
                    assignment["room_id"],
                    assignment["period_start"]
                )
                if key not in seen:
                    seen[key] = assignment
                    deduplicated.append(assignment)
            return deduplicated
        
        # Otherwise, group consecutive periods (for period_length=1 case)
        # Group assignments by (course_id, week, day, room_id)
        grouped = {}
        for assignment in assignments:
            key = (
                assignment["course_id"],
                assignment["week"],
                assignment["day"],
                assignment["room_id"]
            )
            
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(assignment)
        
        # Merge consecutive periods
        merged_assignments = []
        for key, group in grouped.items():
            # Sort by period_start
            group.sort(key=lambda x: x["period_start"])
            
            # Find consecutive periods
            current_start = group[0]["period_start"]
            current_end = current_start + group[0].get("period_length", 1)
            current_assignment = group[0].copy()
            
            for i in range(1, len(group)):
                next_start = group[i]["period_start"]
                next_length = group[i].get("period_length", 1)
                
                if next_start == current_end:
                    # Consecutive period - merge
                    current_end = next_start + next_length
                    current_assignment["period_length"] = current_end - current_start
                else:
                    # Gap found, save current assignment and start new one
                    current_assignment["period_length"] = current_end - current_start
                    merged_assignments.append(current_assignment)
                    
                    current_start = next_start
                    current_end = next_start + next_length
                    current_assignment = group[i].copy()
            
            # Save last assignment
            current_assignment["period_length"] = current_end - current_start
            merged_assignments.append(current_assignment)
        
        return merged_assignments
    
    def solve_what_if(
        self,
        input_json: Dict[str, Any],
        query_constraints: List[Dict[str, Any]],
        original_objective: float
    ) -> Dict[str, Any]:
        """
        Solve What-If query using UDSP (User-Desired Satisfiability Problem)
        Based on X-MILP paper Section 4.2
        
        Args:
            input_json: Original scheduling input
            query_constraints: List of query constraint dicts from QueryTranslator
            original_objective: Objective value from original optimal solution
        
        Returns:
            Dict with status, IIS (if infeasible), or alternative schedule (if feasible)
        """
        if self.use_julia_solver:
            return self._solve_what_if_julia(input_json, query_constraints, original_objective)
        else:
            print("⚠️ Mock solver does not support what-if analysis")
            return {
                "status": "not_supported",
                "query_feasible": False,
                "explanation": "What-if analysis not supported in mock mode"
            }
    
    def _solve_what_if_julia(
        self,
        input_json: Dict[str, Any],
        query_constraints: List[Dict[str, Any]],
        original_objective: float
    ) -> Dict[str, Any]:
        """Call Julia what-if solver"""
        try:
            print("🔍 Calling Julia what-if solver (UDSP)...")
            
            # Convert query constraints to Julia format
            julia_query_constraints = [qc for qc in query_constraints]
            
            # Call Julia function
            result = self.julia.solve_what_if_query(
                input_json,
                julia_query_constraints,
                original_objective
            )
            
            # Convert back to Python
            python_result = self._julia_to_python(result)
            
            status = python_result.get("status", "unknown")
            print(f"✅ Julia what-if solver returned: {status}")
            
            if status == "feasible_query":
                print(f"   Alternative schedule found with objective: {python_result.get('alternative_objective')}")
            elif status == "infeasible_query":
                iis_count = len(python_result.get("iis", []))
                print(f"   Query infeasible - IIS has {iis_count} constraints")
            
            return python_result
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            print(f"❌ Julia what-if solver error: {error_msg}")
            print(f"   Traceback: {traceback.format_exc()}")
            
            return {
                "status": "error",
                "query_feasible": False,
                "explanation": f"What-if analysis error: {error_msg}",
                "diagnostics": {
                    "error": error_msg,
                    "traceback": traceback.format_exc()
                }
            }
    
    def _mock_solve(self, input_json: Dict[str, Any]) -> Dict[str, Any]:
        """Mock solver for testing without optimization"""
        
        num_courses = len(input_json.get("courses", []))
        num_students = len(input_json.get("students", []))
        
        # Simulate a mostly-good solution with some soft constraint violations
        return {
            "status": "optimal",
            "objective_value": -543.5,
            "solve_time_seconds": 2.3,
            "hard_constraints_ok": True,
            "violated_hard_constraints": [],
            
            "soft_constraint_summary": {
                "S1_student_conflicts": {
                    "count_conflicts": 8,
                    "weighted_penalty": 320.0,
                    "details": "8 students have time conflicts across their enrolled courses"
                }
            },
            
            "schedule": {
                "assignments": [
                    {
                        "course_id": "MS&E252",
                        "course_name": "Decision Analysis",
                        "room_id": "Rm101",
                        "room_name": "Smith Hall 101",
                        "week": 3,
                        "day": "Tue",
                        "period_start": 4,
                        "period_length": 1,
                        "instructor_id": "Prof_A",
                        "instructor_name": "Prof. Anderson"
                    }
                ]
            },
            
            "diagnostics": {
                "room_capacity_violations": [],
                "teacher_overlaps": [],
                "student_conflicts": [],
                "lunch_violations": []
            },
            
            "metadata": {
                "constraint_metadata_version": "1.0",
                "solver_version": "mock_v0.1"
            }
        }
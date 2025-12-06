from typing import Dict, Any, Optional
import os
import sys

# Add parent directory to path to import config.py from project root
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from solver_interface import SolverInterface
from explanation_agent import ExplanationAgent
from storage import RunStorage
from config import Config


class SchedulingPipeline:
    """Main orchestrator"""
    
    def __init__(self, solver_type: str = "julia"):
        """
        Initialize pipeline components
        
        Args:
            solver_type: "julia" (default) or "mock" (testing)
        """
        use_julia = (solver_type == "julia")
        
        self.solver = SolverInterface(use_julia_solver=use_julia)
        self.explainer = ExplanationAgent()
        self.storage = RunStorage()
        
        self.current_run_id = None
        self.previous_run_id = None
    
    def run_optimization(
        self,
        input_json: Dict[str, Any],
        save: bool = True
    ) -> tuple[str, Dict[str, Any]]:
        """
        Run optimization and optionally save
        
        Args:
            input_json: Scheduling input
            save: Whether to save the run
        
        Returns:
            (run_id, solver_output)
        """
        print("🔧 Running optimization solver...")
        
        solver_output = self.solver.solve(input_json)
        
        print(f"✅ Optimization complete: {solver_output['status']}")
        
        if save:
            # Shift run IDs
            self.previous_run_id = self.current_run_id
            self.current_run_id = self.storage.save_run(input_json, solver_output)
            print(f"💾 Saved as: {self.current_run_id}")
        
        return self.current_run_id, solver_output
    
    def explain_current_schedule(self, question: str = None) -> str:
        """
        Explain the most recent optimization result
        
        Args:
            question: Optional specific question
        
        Returns:
            Natural language explanation
        """
        if not self.current_run_id:
            return "No optimization has been run yet."
        
        print(f"💬 Generating explanation...")
        
        run_data = self.storage.load_run(self.current_run_id)
        
        input_summary = self._summarize_input(run_data['input'])
        
        explanation = self.explainer.explain_schedule(
            input_summary=input_summary,
            solver_output=run_data['output'],
            question=question,
            full_input=run_data['input']  # Pass full input for detailed analysis
        )
        
        print("✅ Explanation generated\n")
        return explanation
    
    def compare_with_previous(self, question: str = None) -> str:
        """
        Compare current schedule with previous one
        
        Args:
            question: Optional specific question
        
        Returns:
            Explanation of differences
        """
        if not self.current_run_id or not self.previous_run_id:
            return "Need both current and previous runs to compare."
        
        print(f"🔄 Comparing schedules...")
        
        old_run = self.storage.load_run(self.previous_run_id)
        new_run = self.storage.load_run(self.current_run_id)
        
        explanation = self.explainer.compare_schedules(
            old_run=old_run,
            new_run=new_run,
            question=question
        )
        
        print("✅ Comparison generated\n")
        return explanation
    
    def explain_run_by_id(self, run_id: str, question: str = None) -> str:
        """Explain a specific run by ID"""
        run_data = self.storage.load_run(run_id)
        input_summary = self._summarize_input(run_data['input'])
        
        return self.explainer.explain_schedule(
            input_summary=input_summary,
            solver_output=run_data['output'],
            question=question,
            full_input=run_data['input']  # Pass full input for detailed analysis
        )
    
    def _summarize_input(self, input_json: Dict[str, Any]) -> Dict[str, Any]:
        """Create a summary of input for explanation context"""
        return {
            "num_courses": len(input_json.get("courses", [])),
            "num_instructors": len(input_json.get("instructors", [])),
            "num_students": len(input_json.get("students", [])),
            "num_classrooms": len(input_json.get("classrooms", [])),
            "term_weeks": input_json.get("term_config", {}).get("num_weeks", "N/A"),
            "days_per_week": len(input_json.get("term_config", {}).get("days", []))
        }
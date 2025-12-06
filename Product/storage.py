import sqlite3
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path to import config.py from project root
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from config import Config


class SchedulingDatabase:
    """SQLite database manager for course scheduling system"""
    
    def __init__(self, db_path: str = "scheduling.db"):
        self.db_path = db_path
        self.conn = None
        self.init_database()
    
    def init_database(self):
        """Create all tables if they don't exist"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Enable column access by name
        
        cursor = self.conn.cursor()
        
        # Runs table - stores optimization runs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                objective_value REAL,
                solve_time_seconds REAL,
                hard_constraints_ok BOOLEAN,
                num_assignments INTEGER DEFAULT 0,
                num_conflicts INTEGER DEFAULT 0,
                input_json TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Courses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                weekly_hours REAL NOT NULL,
                instructor_id TEXT NOT NULL,
                expected_enrollment INTEGER,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Instructors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS instructors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                back_to_back_preference INTEGER,
                allow_lunch_teaching BOOLEAN,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Classrooms table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS classrooms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Students table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                name TEXT,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Enrollments table (many-to-many)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS enrollments (
                student_id TEXT,
                course_id TEXT,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (student_id, course_id),
                FOREIGN KEY (student_id) REFERENCES students(id),
                FOREIGN KEY (course_id) REFERENCES courses(id)
            )
        ''')
        
        # Assignments table - stores schedule assignments from successful runs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                course_id TEXT NOT NULL,
                course_name TEXT,
                room_id TEXT NOT NULL,
                room_name TEXT,
                instructor_id TEXT,
                instructor_name TEXT,
                week INTEGER NOT NULL,
                day TEXT NOT NULL,
                period_start INTEGER NOT NULL,
                period_length INTEGER NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id),
                FOREIGN KEY (course_id) REFERENCES courses(id),
                FOREIGN KEY (room_id) REFERENCES classrooms(id)
            )
        ''')
        
        # Conflicts table - stores student conflicts from runs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                course1_id TEXT NOT NULL,
                course2_id TEXT NOT NULL,
                week INTEGER,
                day TEXT,
                period INTEGER,
                conflict_type TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            )
        ''')
        
        # Create indices for better query performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_assignments_run ON assignments(run_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conflicts_run ON conflicts(run_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_enrollments_course ON enrollments(course_id)')
        
        self.conn.commit()
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def __del__(self):
        """Cleanup on deletion"""
        self.close()


class RunStorage:
    """Manages persistent storage of optimization runs using SQLite"""
    
    def __init__(self, db_path: str = None):
        """
        Initialize storage with SQLite database
        
        Args:
            db_path: Path to SQLite database file (default: scheduling.db in current directory)
        """
        if db_path is None:
            # Use database in current directory (Product/) instead of subdirectory
            db_path = "scheduling.db"
        
        # Ensure parent directory exists
        db_dir = os.path.dirname(db_path) if os.path.dirname(db_path) else "."
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        self.db = SchedulingDatabase(db_path)
    
    def save_run(
        self,
        input_json: Dict[str, Any],
        solver_output: Dict[str, Any],
        run_id: str = None
    ) -> str:
        """
        Save an optimization run to database
        
        Args:
            input_json: Input parameters
            solver_output: Solver output
            run_id: Optional run ID (generated if not provided)
        
        Returns:
            Run ID
        """
        if not run_id:
            run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        cursor = self.db.conn.cursor()
        
        # Count assignments and conflicts
        num_assignments = len(solver_output.get('schedule', {}).get('assignments', []))
        num_conflicts = len(solver_output.get('diagnostics', {}).get('student_conflicts', []))
        
        # Save run metadata
        cursor.execute('''
            INSERT OR REPLACE INTO runs 
            (run_id, timestamp, status, objective_value, solve_time_seconds, 
             hard_constraints_ok, num_assignments, num_conflicts, input_json, output_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            run_id,
            datetime.now().isoformat(),
            solver_output['status'],
            solver_output.get('objective_value'),
            solver_output.get('solve_time_seconds'),
            solver_output.get('hard_constraints_ok', False),
            num_assignments,
            num_conflicts,
            json.dumps(input_json),
            json.dumps(solver_output)
        ))
        
        # Save entities (courses, instructors, classrooms, students)
        self._save_entities(input_json)
        
        # Save assignments and conflicts if optimal
        if solver_output['status'] == 'optimal':
            self._save_assignments(run_id, solver_output)
            self._save_conflicts(run_id, solver_output)
        
        self.db.conn.commit()
        
        print(f"💾 Saved run {run_id} to database")
        return run_id
    
    def _save_entities(self, input_json: Dict[str, Any]):
        """Save courses, instructors, classrooms, students to database"""
        cursor = self.db.conn.cursor()
        timestamp = datetime.now().isoformat()
        
        # Save courses
        for course in input_json.get('courses', []):
            cursor.execute('''
                INSERT OR REPLACE INTO courses 
                (id, name, type, weekly_hours, instructor_id, expected_enrollment, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                course['id'],
                course['name'],
                course['type'],
                course['weekly_hours'],
                course['instructor_id'],
                course.get('expected_enrollment', 0),
                timestamp
            ))
        
        # Save instructors
        for instructor in input_json.get('instructors', []):
            cursor.execute('''
                INSERT OR REPLACE INTO instructors 
                (id, name, back_to_back_preference, allow_lunch_teaching, last_updated)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                instructor['id'],
                instructor['name'],
                instructor.get('back_to_back_preference', 0),
                instructor.get('allow_lunch_teaching', False),
                timestamp
            ))
        
        # Save classrooms
        for classroom in input_json.get('classrooms', []):
            cursor.execute('''
                INSERT OR REPLACE INTO classrooms 
                (id, name, capacity, last_updated)
                VALUES (?, ?, ?, ?)
            ''', (
                classroom['id'],
                classroom['name'],
                classroom['capacity'],
                timestamp
            ))
        
        # Save students
        for student in input_json.get('students', []):
            cursor.execute('''
                INSERT OR REPLACE INTO students 
                (id, name, last_updated)
                VALUES (?, ?, ?)
            ''', (
                student['id'],
                student.get('name', ''),
                timestamp
            ))
            
            # Save enrollments
            for course_id in student.get('enrolled_course_ids', []):
                cursor.execute('''
                    INSERT OR REPLACE INTO enrollments 
                    (student_id, course_id, last_updated)
                    VALUES (?, ?, ?)
                ''', (student['id'], course_id, timestamp))
    
    def _save_assignments(self, run_id: str, solver_output: Dict[str, Any]):
        """Save schedule assignments for a run"""
        cursor = self.db.conn.cursor()
        
        # Clear existing assignments for this run
        cursor.execute('DELETE FROM assignments WHERE run_id = ?', (run_id,))
        
        # Save new assignments
        for assignment in solver_output.get('schedule', {}).get('assignments', []):
            cursor.execute('''
                INSERT INTO assignments 
                (run_id, course_id, course_name, room_id, room_name, 
                 instructor_id, instructor_name, week, day, period_start, period_length)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                run_id,
                assignment['course_id'],
                assignment.get('course_name', ''),
                assignment['room_id'],
                assignment.get('room_name', ''),
                assignment.get('instructor_id', ''),
                assignment.get('instructor_name', ''),
                assignment['week'],
                assignment['day'],
                assignment['period_start'],
                assignment['period_length']
            ))
    
    def _save_conflicts(self, run_id: str, solver_output: Dict[str, Any]):
        """Save student conflicts for a run"""
        cursor = self.db.conn.cursor()
        
        # Clear existing conflicts for this run
        cursor.execute('DELETE FROM conflicts WHERE run_id = ?', (run_id,))
        
        # Save new conflicts
        for conflict in solver_output.get('diagnostics', {}).get('student_conflicts', []):
            cursor.execute('''
                INSERT INTO conflicts 
                (run_id, student_id, course1_id, course2_id, week, day, period, conflict_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                run_id,
                conflict.get('student_id', ''),
                conflict.get('course1_id', ''),
                conflict.get('course2_id', ''),
                conflict.get('week'),
                conflict.get('day'),
                conflict.get('period'),
                conflict.get('conflict_type', 'time_overlap')
            ))
    
    def load_run(self, run_id: str) -> Dict[str, Any]:
        """
        Load a run by ID
        
        Args:
            run_id: Run identifier
        
        Returns:
            Run data dictionary with input and output
        """
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT run_id, timestamp, input_json, output_json
            FROM runs
            WHERE run_id = ?
        ''', (run_id,))
        
        row = cursor.fetchone()
        if not row:
            raise FileNotFoundError(f"Run {run_id} not found in database")
        
        return {
            'run_id': row['run_id'],
            'timestamp': row['timestamp'],
            'input': json.loads(row['input_json']),
            'output': json.loads(row['output_json'])
        }
    
    def list_runs(self, limit: int = None, status: str = None) -> List[str]:
        """
        List all saved run IDs
        
        Args:
            limit: Maximum number of runs to return
            status: Filter by status (optimal, infeasible, etc.)
        
        Returns:
            List of run IDs ordered by timestamp (newest first)
        """
        cursor = self.db.conn.cursor()
        
        query = 'SELECT run_id FROM runs'
        params = []
        
        if status:
            query += ' WHERE status = ?'
            params.append(status)
        
        query += ' ORDER BY timestamp DESC'
        
        if limit:
            query += ' LIMIT ?'
            params.append(limit)
        
        cursor.execute(query, params)
        return [row['run_id'] for row in cursor.fetchall()]
    
    def get_latest_run(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent run
        
        Returns:
            Run data or None if no runs exist
        """
        runs = self.list_runs(limit=1)
        if not runs:
            return None
        return self.load_run(runs[0])
    
    def get_run_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent optimization runs with summary information
        
        Args:
            limit: Maximum number of runs to return
        
        Returns:
            List of run summaries
        """
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT run_id, timestamp, status, objective_value, 
                   solve_time_seconds, hard_constraints_ok,
                   num_assignments, num_conflicts
            FROM runs
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_schedule_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """
        Get all assignments for a specific run
        
        Args:
            run_id: Run identifier
        
        Returns:
            List of assignments
        """
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT * FROM assignments
            WHERE run_id = ?
            ORDER BY week, day, period_start
        ''', (run_id,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_conflicts_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """
        Get student conflicts for a specific run
        
        Args:
            run_id: Run identifier
        
        Returns:
            List of conflicts
        """
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT c.*, 
                   c1.name as course1_name,
                   c2.name as course2_name
            FROM conflicts c
            LEFT JOIN courses c1 ON c.course1_id = c1.id
            LEFT JOIN courses c2 ON c.course2_id = c2.id
            WHERE c.run_id = ?
        ''', (run_id,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_run_statistics(self) -> Dict[str, Any]:
        """
        Get overall statistics across all runs
        
        Returns:
            Dictionary with statistics
        """
        cursor = self.db.conn.cursor()
        
        # Total runs
        cursor.execute('SELECT COUNT(*) as total FROM runs')
        total_runs = cursor.fetchone()['total']
        
        # Runs by status
        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM runs
            GROUP BY status
        ''')
        status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
        
        # Average solve time for optimal runs
        cursor.execute('''
            SELECT AVG(solve_time_seconds) as avg_time
            FROM runs
            WHERE status = 'optimal'
        ''')
        avg_solve_time = cursor.fetchone()['avg_time'] or 0
        
        # Average conflicts for optimal runs
        cursor.execute('''
            SELECT AVG(num_conflicts) as avg_conflicts
            FROM runs
            WHERE status = 'optimal'
        ''')
        avg_conflicts = cursor.fetchone()['avg_conflicts'] or 0
        
        return {
            'total_runs': total_runs,
            'status_counts': status_counts,
            'avg_solve_time': round(avg_solve_time, 2),
            'avg_conflicts': round(avg_conflicts, 2)
        }
    
    def compare_runs(self, run_id1: str, run_id2: str) -> Dict[str, Any]:
        """
        Compare two optimization runs
        
        Args:
            run_id1: First run ID
            run_id2: Second run ID
        
        Returns:
            Comparison dictionary
        """
        run1 = self.load_run(run_id1)
        run2 = self.load_run(run_id2)
        
        schedule1 = self.get_schedule_for_run(run_id1)
        schedule2 = self.get_schedule_for_run(run_id2)
        
        # Compare assignments
        assignments1 = {a['course_id']: a for a in schedule1}
        assignments2 = {a['course_id']: a for a in schedule2}
        
        changed = []
        for course_id in set(assignments1.keys()) | set(assignments2.keys()):
            if course_id not in assignments1:
                changed.append({
                    'course_id': course_id,
                    'change': 'added',
                    'new_assignment': assignments2[course_id]
                })
            elif course_id not in assignments2:
                changed.append({
                    'course_id': course_id,
                    'change': 'removed',
                    'old_assignment': assignments1[course_id]
                })
            else:
                a1, a2 = assignments1[course_id], assignments2[course_id]
                if (a1['day'] != a2['day'] or 
                    a1['period_start'] != a2['period_start'] or
                    a1['room_id'] != a2['room_id']):
                    changed.append({
                        'course_id': course_id,
                        'change': 'modified',
                        'old_assignment': a1,
                        'new_assignment': a2
                    })
        
        return {
            'run1': {
                'run_id': run_id1,
                'status': run1['output']['status'],
                'objective_value': run1['output'].get('objective_value'),
                'num_conflicts': len(self.get_conflicts_for_run(run_id1))
            },
            'run2': {
                'run_id': run_id2,
                'status': run2['output']['status'],
                'objective_value': run2['output'].get('objective_value'),
                'num_conflicts': len(self.get_conflicts_for_run(run_id2))
            },
            'changed_assignments': changed
        }
    
    def get_courses(self) -> List[Dict[str, Any]]:
        """Get all courses from database"""
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT * FROM courses')
        return [dict(row) for row in cursor.fetchall()]
    
    def get_instructors(self) -> List[Dict[str, Any]]:
        """Get all instructors from database"""
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT * FROM instructors')
        return [dict(row) for row in cursor.fetchall()]
    
    def get_classrooms(self) -> List[Dict[str, Any]]:
        """Get all classrooms from database"""
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT * FROM classrooms')
        return [dict(row) for row in cursor.fetchall()]
    
    def get_students(self) -> List[Dict[str, Any]]:
        """Get all students with their enrollments"""
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT s.id, s.name,
                   GROUP_CONCAT(e.course_id) as enrolled_courses
            FROM students s
            LEFT JOIN enrollments e ON s.id = e.student_id
            GROUP BY s.id, s.name
        ''')
        
        students = []
        for row in cursor.fetchall():
            student = dict(row)
            if student['enrolled_courses']:
                student['enrolled_course_ids'] = student['enrolled_courses'].split(',')
            else:
                student['enrolled_course_ids'] = []
            del student['enrolled_courses']
            students.append(student)
        
        return students
    
    def delete_run(self, run_id: str):
        """
        Delete a run and all its associated data
        
        Args:
            run_id: Run identifier
        """
        cursor = self.db.conn.cursor()
        
        cursor.execute('DELETE FROM assignments WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM conflicts WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM runs WHERE run_id = ?', (run_id,))
        
        self.db.conn.commit()
        print(f"🗑️  Deleted run {run_id}")
    
    def clear_all_runs(self):
        """Delete all runs (use with caution!)"""
        cursor = self.db.conn.cursor()
        
        cursor.execute('DELETE FROM assignments')
        cursor.execute('DELETE FROM conflicts')
        cursor.execute('DELETE FROM runs')
        
        self.db.conn.commit()
        print("🗑️  Cleared all runs from database")
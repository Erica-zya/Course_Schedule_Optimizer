"""
Large-Scale Scheduling Input Generator

Generates realistic scheduling inputs with 50+ courses, 30+ professors, 1000+ students
Based on actual university patterns and constraints.

Usage:
    python generate_large_input.py --output large_schedule_input.json
"""

import json
import argparse
import random
import numpy as np
from typing import Dict, Any, List
from datetime import datetime, timedelta


class LargeScaleInputGenerator:
    """
    Generate realistic large-scale scheduling inputs
    """
    
    # Course type distributions (realistic for a university)
    COURSE_TYPES = {
        "full_term": 0.70,        # 70% full semester courses
        "first_half_term": 0.15,  # 15% first half
        "second_half_term": 0.15  # 15% second half
    }
    
    # Department structure
    DEPARTMENTS = [
        "Computer Science", "Mathematics", "Statistics", "Economics",
        "Management Science & Engineering", "Electrical Engineering",
        "Mechanical Engineering", "Physics", "Chemistry", "Biology"
    ]
    
    # Course levels and typical enrollments
    COURSE_LEVELS = {
        "100": {"min_enroll": 80, "max_enroll": 200, "prob": 0.15},   # Intro courses
        "200": {"min_enroll": 40, "max_enroll": 100, "prob": 0.30},   # Lower division
        "300": {"min_enroll": 20, "max_enroll": 60, "prob": 0.35},    # Upper division
        "400": {"min_enroll": 10, "max_enroll": 30, "prob": 0.20},    # Advanced/grad
    }
    
    # Student year distributions
    STUDENT_YEARS = {
        "freshman": 0.25,
        "sophomore": 0.25,
        "junior": 0.25,
        "senior": 0.20,
        "graduate": 0.05
    }
    
    def __init__(self, seed: int = 42):
        """
        Initialize generator with random seed for reproducibility
        """
        random.seed(seed)
        np.random.seed(seed)
    
    def generate_courses(
        self,
        num_courses: int,
        num_instructors: int
    ) -> List[Dict[str, Any]]:
        """
        Generate course catalog with realistic distributions
        """
        courses = []
        course_names = self._generate_course_names(num_courses)
        
        for i in range(num_courses):
            # Determine course level
            level = random.choices(
                list(self.COURSE_LEVELS.keys()),
                weights=[v["prob"] for v in self.COURSE_LEVELS.values()]
            )[0]
            
            # Determine expected enrollment based on level
            level_info = self.COURSE_LEVELS[level]
            enrollment = random.randint(level_info["min_enroll"], level_info["max_enroll"])
            
            # Determine course type
            course_type = random.choices(
                list(self.COURSE_TYPES.keys()),
                weights=list(self.COURSE_TYPES.values())
            )[0]
            
            # Assign instructor (roughly equal distribution)
            instructor_idx = i % num_instructors
            
            # Determine weekly hours (most courses are 1.5 hours/week for lecture)
            # Some upper-level courses might have labs (3.0 hours)
            if level in ["100", "200"]:
                weekly_hours = 1.5
            else:
                # 70% lecture only (1.5h), 30% with lab (3.0h)
                weekly_hours = 1.5 if random.random() < 0.7 else 3.0
            
            courses.append({
                "id": f"COURSE{i:03d}",
                "name": course_names[i],
                "type": course_type,
                "weekly_hours": weekly_hours,
                "instructor_id": f"PROF{instructor_idx:03d}",
                "expected_enrollment": enrollment,
                "level": level,
                "department": random.choice(self.DEPARTMENTS)
            })
        
        return courses
    
    def _generate_course_names(self, num_courses: int) -> List[str]:
        """
        Generate realistic course names
        """
        # Common course name patterns
        prefixes = [
            "CS", "MATH", "STAT", "ECON", "MS&E", "EE", "ME", "PHYS", "CHEM", "BIO",
            "ENGR", "DATA", "HIST", "ENGL", "PSYCH", "SOC", "PHIL"
        ]
        
        names = []
        for i in range(num_courses):
            prefix = random.choice(prefixes)
            number = random.randint(100, 499)
            # Add descriptive suffix occasionally
            suffixes = [
                "Introduction", "Advanced", "Theory", "Applications",
                "Seminar", "Lab", "Workshop", "Project"
            ]
            if random.random() < 0.3:
                name = f"{prefix} {number}: {random.choice(suffixes)}"
            else:
                name = f"{prefix} {number}"
            names.append(name)
        
        return names
    
    def generate_instructors(
        self,
        num_instructors: int,
        num_courses: int,
        courses: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate instructor profiles with realistic availability patterns
        Ensures enough availability to teach assigned courses
        """
        instructors = []
        
        # First/last names for variety
        first_names = [
            "Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry",
            "Iris", "Jack", "Karen", "Leo", "Maria", "Nathan", "Olivia", "Peter",
            "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
            "Yuki", "Zoe", "Alex", "Blake", "Casey", "Drew", "Eli", "Fiona"
        ]
        last_names = [
            "Anderson", "Brown", "Chen", "Davis", "Evans", "Fischer", "Garcia",
            "Harris", "Ivanov", "Johnson", "Kim", "Lee", "Martinez", "Nguyen",
            "O'Brien", "Patel", "Quinn", "Rodriguez", "Smith", "Taylor", "Ueda",
            "Vargas", "Wang", "Xu", "Yamamoto", "Zhang"
        ]
        
        # Calculate how many hours each instructor needs to teach
        courses_per_instructor = {}
        total_hours_per_instructor = {}
        for course in courses:
            inst_id = course["instructor_id"]
            courses_per_instructor[inst_id] = courses_per_instructor.get(inst_id, 0) + 1
            total_hours_per_instructor[inst_id] = total_hours_per_instructor.get(inst_id, 0) + course["weekly_hours"]
        
        for i in range(num_instructors):
            first = random.choice(first_names)
            last = random.choice(last_names)
            inst_id = f"PROF{i:03d}"
            
            # Calculate required availability
            num_courses_to_teach = courses_per_instructor.get(inst_id, 0)
            hours_to_teach = total_hours_per_instructor.get(inst_id, 0)
            
            # Convert hours to periods (30min each = 2 periods per hour)
            required_periods = int(hours_to_teach * 2)
            
            # Add buffer: need at least 3x the required periods for flexibility
            # (to allow for pattern consistency constraint and scheduling flexibility)
            min_periods_needed = max(required_periods * 3, 30)  # At least 30 periods minimum
            
            # Generate availability with sufficient capacity
            # Start with 4-5 days per week for more flexibility
            num_days_available = random.choice([4, 5])
            available_days = random.sample(["Mon", "Tue", "Wed", "Thu", "Fri"], num_days_available)
            
            availability = []
            for day in available_days:
                # For feasibility, use longer availability windows
                # Most professors available 8am-5pm (periods 0-18)
                if random.random() < 0.2:
                    # Morning person (8am-2pm) - only if they have few courses
                    if num_courses_to_teach <= 2:
                        period_range = range(0, 12)
                    else:
                        period_range = range(0, 18)
                elif random.random() < 0.2:
                    # Afternoon person (11am-6pm) - only if they have few courses
                    if num_courses_to_teach <= 2:
                        period_range = range(6, 20)
                    else:
                        period_range = range(0, 18)
                else:
                    # Full day (8am-6pm) - most common
                    period_range = range(0, 20)
                
                for period in period_range:
                    availability.append({"day": day, "period_index": period})
            
            # Verify we have enough availability
            total_available_periods = len(availability)
            if total_available_periods < min_periods_needed:
                # Add more days if needed
                remaining_days = [d for d in ["Mon", "Tue", "Wed", "Thu", "Fri"] if d not in available_days]
                while total_available_periods < min_periods_needed and remaining_days:
                    extra_day = remaining_days.pop(0)
                    for period in range(0, 20):  # Full day availability
                        availability.append({"day": extra_day, "period_index": period})
                    total_available_periods += 20
            
            # Back-to-back preference (-1 = prefer, 0 = neutral, 1 = avoid)
            # Most professors slightly prefer or are neutral
            b2b_pref = random.choices([-1, 0, 1], weights=[0.3, 0.5, 0.2])[0]
            
            # Lunch teaching (most avoid, some don't mind)
            allow_lunch = random.random() < 0.3
            
            instructors.append({
                "id": inst_id,
                "name": f"Prof. {first} {last}",
                "availability": availability,
                "back_to_back_preference": b2b_pref,
                "allow_lunch_teaching": allow_lunch,
                "department": random.choice(self.DEPARTMENTS),
                "_debug": {
                    "courses_to_teach": num_courses_to_teach,
                    "hours_to_teach": hours_to_teach,
                    "available_periods": len(availability),
                    "required_min_periods": min_periods_needed
                }
            })
        
        return instructors
    
    def generate_classrooms(
        self,
        num_rooms: int,
        courses: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate classroom inventory with realistic capacities
        Ensures sufficient capacity for all courses
        """
        classrooms = []
        
        # Building names
        buildings = [
            "Gates", "Huang", "Hewlett", "McCullough", "Littlefield",
            "Meyer", "Jordan", "Skilling", "Mitchell", "Thornton"
        ]
        
        # Find max enrollment to ensure at least one room can fit it
        max_enrollment = max(c["expected_enrollment"] for c in courses)
        
        # Capacity distribution (realistic for university)
        # Ensure we have rooms that can fit all courses
        capacity_distribution = [
            (20, 30, 0.10),   # Small seminar rooms
            (30, 50, 0.20),   # Medium seminar rooms
            (50, 80, 0.30),   # Standard classrooms
            (80, 120, 0.25),  # Large lecture halls
            (120, 250, 0.15), # Auditoriums (increased max to handle large courses)
        ]
        
        # Ensure at least one room can fit the largest course
        rooms_added = 0
        for i in range(num_rooms):
            building = random.choice(buildings)
            room_num = random.randint(100, 599)
            
            # For first few rooms, ensure we have large rooms
            if i < 3:
                # Guarantee large rooms for big courses
                capacity = random.randint(max(120, max_enrollment), max(250, max_enrollment + 50))
            else:
                # Select capacity range based on distribution
                cap_min, cap_max, _ = random.choices(
                    capacity_distribution,
                    weights=[w for _, _, w in capacity_distribution]
                )[0]
                capacity = random.randint(cap_min, cap_max)
            
            classrooms.append({
                "id": f"ROOM{i:03d}",
                "name": f"{building} {room_num}",
                "capacity": capacity
            })
            rooms_added += 1
        
        # Verify capacity coverage
        course_enrollments = sorted([c["expected_enrollment"] for c in courses], reverse=True)
        room_capacities = sorted([r["capacity"] for r in classrooms], reverse=True)
        
        print(f"\n  Classroom capacity check:")
        print(f"    Largest course: {course_enrollments[0]} students")
        print(f"    Largest room: {room_capacities[0]} capacity")
        print(f"    Top 5 course sizes: {course_enrollments[:5]}")
        print(f"    Top 5 room capacities: {room_capacities[:5]}")
        
        # Verify each course can fit in at least one room
        for enrollment in course_enrollments:
            if enrollment > room_capacities[0]:
                print(f"    ⚠️  WARNING: Course with {enrollment} students exceeds largest room ({room_capacities[0]})")
        
        return classrooms
    
    def generate_students(
        self,
        num_students: int,
        courses: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate student enrollments with realistic patterns
        
        Students tend to take:
        - Courses at their level (freshmen take 100-level, etc.)
        - Courses in related departments (majors)
        - 3-5 courses per term
        """
        students = []
        
        # Common first names
        names = [
            "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason",
            "Isabella", "William", "Mia", "James", "Charlotte", "Benjamin", "Amelia",
            "Lucas", "Harper", "Henry", "Evelyn", "Alexander", "Abigail", "Michael",
            "Emily", "Daniel", "Elizabeth", "Matthew", "Sofia", "Jackson", "Avery",
            "Sebastian", "Ella", "Jack", "Scarlett", "Aiden", "Grace", "Owen", "Chloe",
            "Samuel", "Victoria", "David", "Riley", "Joseph", "Aria", "Carter", "Lily"
        ]
        
        # Organize courses by level for easier selection
        courses_by_level = {
            "100": [c for c in courses if c["level"] == "100"],
            "200": [c for c in courses if c["level"] == "200"],
            "300": [c for c in courses if c["level"] == "300"],
            "400": [c for c in courses if c["level"] == "400"],
        }
        
        for i in range(num_students):
            # Determine student year
            year = random.choices(
                list(self.STUDENT_YEARS.keys()),
                weights=list(self.STUDENT_YEARS.values())
            )[0]
            
            # Determine number of courses (3-5 typically, graduate students might take fewer)
            if year == "graduate":
                num_courses_to_take = random.choice([2, 3, 4])
            else:
                num_courses_to_take = random.choice([3, 4, 5])
            
            # Determine which levels to take based on year
            if year == "freshman":
                level_probs = {"100": 0.7, "200": 0.3, "300": 0.0, "400": 0.0}
            elif year == "sophomore":
                level_probs = {"100": 0.3, "200": 0.5, "300": 0.2, "400": 0.0}
            elif year == "junior":
                level_probs = {"100": 0.1, "200": 0.3, "300": 0.5, "400": 0.1}
            elif year == "senior":
                level_probs = {"100": 0.0, "200": 0.2, "300": 0.5, "400": 0.3}
            else:  # graduate
                level_probs = {"100": 0.0, "200": 0.0, "300": 0.3, "400": 0.7}
            
            # Select courses based on level preferences
            enrolled_courses = []
            available_courses = courses.copy()
            
            for _ in range(num_courses_to_take):
                if not available_courses:
                    break
                
                # Choose a level based on probability
                level = random.choices(
                    list(level_probs.keys()),
                    weights=list(level_probs.values())
                )[0]
                
                # Get courses at this level that haven't been selected
                candidates = [c for c in available_courses if c["level"] == level]
                
                if not candidates:
                    # Fallback to any available course
                    candidates = available_courses
                
                if candidates:
                    course = random.choice(candidates)
                    enrolled_courses.append(course["id"])
                    available_courses.remove(course)
            
            students.append({
                "id": f"STU{i:04d}",
                "name": f"{random.choice(names)} {i}",
                "year": year,
                "enrolled_course_ids": enrolled_courses
            })
        
        return students
    
    def generate_complete_input(
        self,
        num_courses: int = 60,
        num_instructors: int = 35,
        num_rooms: int = 40,
        num_students: int = 1200,
        num_weeks: int = 10
    ) -> Dict[str, Any]:
        """
        Generate complete scheduling input with all components
        Ensures feasibility through capacity and availability checks
        """
        print(f"Generating large-scale scheduling input...")
        print(f"  Courses: {num_courses}")
        print(f"  Instructors: {num_instructors}")
        print(f"  Rooms: {num_rooms}")
        print(f"  Students: {num_students}")
        print(f"  Term length: {num_weeks} weeks")
        
        # Calculate semester dates
        start_date = datetime(2025, 1, 6)  # Monday
        end_date = start_date + timedelta(weeks=num_weeks) - timedelta(days=1)
        
        # Generate components in order (courses first, then instructors based on courses)
        print("\n  Generating courses...")
        courses = self.generate_courses(num_courses, num_instructors)
        
        print("  Generating instructors with sufficient availability...")
        instructors = self.generate_instructors(num_instructors, num_courses, courses)
        
        print("  Generating classrooms with sufficient capacity...")
        classrooms = self.generate_classrooms(num_rooms, courses)
        
        print("  Generating students...")
        students = self.generate_students(num_students, courses)
        
        # Perform feasibility checks
        print("\n  Performing feasibility checks...")
        self._check_feasibility(courses, instructors, classrooms, num_weeks)
        
        # Calculate statistics
        total_enrollment = sum(len(s["enrolled_course_ids"]) for s in students)
        avg_courses_per_student = total_enrollment / num_students
        
        # Calculate conflict matrix
        conflict_count = 0
        for student in students:
            courses_taken = student["enrolled_course_ids"]
            # Each pair of courses creates a conflict potential
            conflict_count += len(courses_taken) * (len(courses_taken) - 1) // 2
        
        print("\n  Statistics:")
        print(f"    Average courses per student: {avg_courses_per_student:.2f}")
        print(f"    Total enrollments: {total_enrollment}")
        print(f"    Potential conflict pairs: {conflict_count}")
        
        # Clean up debug info from instructors
        for instructor in instructors:
            if "_debug" in instructor:
                del instructor["_debug"]
        
        # Assemble complete input
        input_data = {
            "term_config": {
                "num_weeks": num_weeks,
                "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "period_length_minutes": 30,
                "day_start_time": "08:00",
                "day_end_time": "20:00",
                "lunch_start_time": "12:00",
                "lunch_end_time": "13:00",
                "semester_start_date": start_date.strftime("%Y-%m-%d"),
                "semester_end_date": end_date.strftime("%Y-%m-%d")
            },
            "classrooms": classrooms,
            "instructors": instructors,
            "courses": courses,
            "students": students,
            "conflict_weights": {
                "global_student_conflict_weight": 1.0,
                "instructor_compactness_weight": 1.0,
                "preferred_time_slots_weight": 1.0
            },
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "generator_version": "1.0",
                "description": f"Large-scale scheduling input: {num_courses} courses, {num_instructors} instructors, {num_students} students",
                "feasibility_checked": True,
                "statistics": {
                    "num_courses": num_courses,
                    "num_instructors": num_instructors,
                    "num_rooms": num_rooms,
                    "num_students": num_students,
                    "total_enrollments": total_enrollment,
                    "avg_courses_per_student": avg_courses_per_student,
                    "potential_conflicts": conflict_count
                }
            }
        }
        
        return input_data
    
    def _check_feasibility(
        self,
        courses: List[Dict[str, Any]],
        instructors: List[Dict[str, Any]],
        classrooms: List[Dict[str, Any]],
        num_weeks: int
    ):
        """
        Perform basic feasibility checks to catch obvious infeasibility
        """
        issues = []
        
        # Check 1: Room capacity
        max_room_capacity = max(r["capacity"] for r in classrooms)
        for course in courses:
            if course["expected_enrollment"] > max_room_capacity:
                issues.append(
                    f"❌ Course {course['id']} has {course['expected_enrollment']} students "
                    f"but max room capacity is {max_room_capacity}"
                )
        
        # Check 2: Instructor availability vs teaching load
        instructor_dict = {i["id"]: i for i in instructors}
        courses_by_instructor = {}
        hours_by_instructor = {}
        
        for course in courses:
            inst_id = course["instructor_id"]
            if inst_id not in courses_by_instructor:
                courses_by_instructor[inst_id] = []
                hours_by_instructor[inst_id] = 0
            courses_by_instructor[inst_id].append(course)
            hours_by_instructor[inst_id] += course["weekly_hours"]
        
        for inst_id, inst_courses in courses_by_instructor.items():
            instructor = instructor_dict[inst_id]
            available_periods = len(instructor["availability"])
            available_hours = available_periods * 0.5  # Each period is 30 min
            
            required_hours = hours_by_instructor[inst_id]
            # Need at least 3x the hours for scheduling flexibility
            min_required_availability = required_hours * 3
            
            if available_hours < min_required_availability:
                issues.append(
                    f"❌ {instructor['name']} teaches {required_hours}h but only has "
                    f"{available_hours}h available (need ~{min_required_availability}h for flexibility)"
                )
        
        # Check 3: Total teaching hours vs total available hours
        total_course_hours = sum(c["weekly_hours"] for c in courses)
        total_available_hours = sum(len(i["availability"]) * 0.5 for i in instructors)
        
        # Need at least 3x availability for scheduling flexibility
        if total_available_hours < total_course_hours * 3:
            issues.append(
                f"⚠️  Total teaching load ({total_course_hours}h) may be tight given "
                f"total availability ({total_available_hours}h). Recommend ratio of 3:1 or higher."
            )
        
        # Check 4: Number of time slots vs course sessions needed
        num_days = 5  # Mon-Fri
        num_periods = 24  # 8am-8pm in 30min blocks
        slots_per_week = num_days * num_periods  # 120 slots
        
        # Calculate total sessions needed per week (accounting for course types)
        sessions_per_week = 0
        for course in courses:
            if course["type"] == "full_term":
                sessions_per_week += 1  # One session per week for full term
            elif course["type"] in ["first_half_term", "second_half_term"]:
                sessions_per_week += 2  # Two sessions per week for half-term (doubled pace)
        
        # Also account for multi-period courses
        total_periods_needed = sum(
            (c["weekly_hours"] * 2)  # Convert hours to periods (30min each)
            for c in courses
        )
        
        utilization = total_periods_needed / (slots_per_week * len(classrooms))
        
        print(f"    Time slot utilization: {utilization*100:.1f}%")
        if utilization > 0.5:
            issues.append(
                f"⚠️  High time slot utilization ({utilization*100:.1f}%). "
                f"Recommend keeping below 50% for flexibility."
            )
        
        # Print results
        if issues:
            print(f"\n  ⚠️  Feasibility warnings ({len(issues)}):")
            for issue in issues:
                print(f"    {issue}")
            print(f"\n  Note: These are warnings. The optimizer may still find a solution.")
        else:
            print(f"    ✅ All basic feasibility checks passed!")
            print(f"    ✅ Total teaching load: {total_course_hours}h")
            print(f"    ✅ Total availability: {total_available_hours}h")
            print(f"    ✅ Availability ratio: {total_available_hours/total_course_hours:.1f}:1")
    
    def save_to_file(self, input_data: Dict[str, Any], filename: str):
        """
        Save generated input to JSON file
        """
        with open(filename, 'w') as f:
            json.dump(input_data, f, indent=2)
        
        file_size_mb = len(json.dumps(input_data)) / (1024 * 1024)
        print(f"\n✅ Saved to {filename}")
        print(f"   File size: {file_size_mb:.2f} MB")
    
    def generate_multiple_scenarios(self, output_dir: str = "large_inputs"):
        """
        Generate multiple scenarios with different scales
        """
        import os
        os.makedirs(output_dir, exist_ok=True)
        
        scenarios = [
            {
                "name": "medium_scale",
                "courses": 50,
                "instructors": 30,
                "rooms": 35,
                "students": 1000,
                "weeks": 10
            },
            {
                "name": "large_scale",
                "courses": 75,
                "instructors": 40,
                "rooms": 50,
                "students": 1500,
                "weeks": 10
            },
            {
                "name": "very_large_scale",
                "courses": 100,
                "instructors": 50,
                "rooms": 60,
                "students": 2000,
                "weeks": 10
            }
        ]
        
        for scenario in scenarios:
            print(f"\n{'='*70}")
            print(f"Generating scenario: {scenario['name']}")
            print(f"{'='*70}")
            
            input_data = self.generate_complete_input(
                num_courses=scenario["courses"],
                num_instructors=scenario["instructors"],
                num_rooms=scenario["rooms"],
                num_students=scenario["students"],
                num_weeks=scenario["weeks"]
            )
            
            filename = f"{output_dir}/{scenario['name']}_input.json"
            self.save_to_file(input_data, filename)


def main():
    parser = argparse.ArgumentParser(description="Generate large-scale scheduling inputs")
    parser.add_argument("--courses", type=int, default=50, help="Number of courses")
    parser.add_argument("--instructors", type=int, default=30, help="Number of instructors")
    parser.add_argument("--rooms", type=int, default=20, help="Number of classrooms")
    parser.add_argument("--students", type=int, default=1000, help="Number of students")
    parser.add_argument("--weeks", type=int, default=14, help="Term length in weeks")
    parser.add_argument("--output", type=str, default="large_schedule_input.json", help="Output filename")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--multiple", action="store_true", help="Generate multiple scenarios")
    
    args = parser.parse_args()
    
    generator = LargeScaleInputGenerator(seed=args.seed)
    
    if args.multiple:
        generator.generate_multiple_scenarios()
    else:
        input_data = generator.generate_complete_input(
            num_courses=args.courses,
            num_instructors=args.instructors,
            num_rooms=args.rooms,
            num_students=args.students,
            num_weeks=args.weeks
        )
        
        generator.save_to_file(input_data, args.output)
        
        print("\n📊 To run optimization on this input:")
        print(f"   python main.py run --input {args.output}")
        print("\n   Or via API:")
        print(f"   curl -X POST http://localhost:8000/optimize -H 'Content-Type: application/json' -d @{args.output}")


if __name__ == "__main__":
    main()
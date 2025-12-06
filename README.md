# Stanford Navigate Enrollment - Course Schedule Optimizer

**Stanford CME 307 Optimization Final Project**
Course Website: https://stanford-cme-307.github.io/web/

An intelligent course scheduling optimization system that uses mixed-integer linear programming (MILP) to generate optimal course schedules while satisfying hard constraints and minimizing soft constraint violations. The system includes AI-powered explanations, what-if analysis, and a modern web interface.

## Overview

This system solves the complex problem of scheduling university courses by:
- Assigning courses to time slots, rooms, and instructors
- Satisfying hard constraints (room capacity, instructor availability, course duration)
- Minimizing soft constraint violations (student conflicts, instructor preferences)
- Providing AI-powered explanations for scheduling decisions
- Enabling what-if analysis to explore alternative schedules

## Key Features

### Optimization Engine
- **MILP Solver**: Uses Julia/JuMP with Gurobi optimizer for high-performance optimization
- **Greedy Warm Start**: Provides initial feasible solutions to accelerate convergence
- **Time-Limited Optimization**: Returns best feasible solution even if optimal solution isn't found within time limit
- **Multi-Objective Optimization**: Balances student conflicts, instructor preferences, and time slot preferences

### AI-Powered Explanations
- **Natural Language Explanations**: Uses Google Gemini AI to explain scheduling decisions
- **Constraint Analysis**: Identifies and explains hard constraint violations
- **Conflict Detection**: Highlights student scheduling conflicts
- **Comparison Tools**: Compares different schedule runs to explain changes

### What-If Analysis
- **Alternative Schedule Generation**: Explores feasible alternatives to current schedule
- **IIS (Irreducible Infeasible Set) Detection**: Identifies minimal sets of conflicting constraints
- **Query Translation**: Converts natural language queries into optimization constraints

### Visualization & Interface
- **Interactive Calendar View**: Visual schedule display with per-professor color coding
- **Overlap Detection**: Automatically handles and displays overlapping courses
- **Hover Tooltips**: Detailed course information on hover
- **Week Navigation**: Browse schedules across different weeks
- **Run History**: Track and compare optimization runs

### Batch Processing
- **Large-Scale Testing**: Process multiple input files in batch
- **Progress Tracking**: Monitor optimization progress over time
- **Performance Metrics**: Extract and visualize solver performance data

## Architecture

### Backend Components
- **FastAPI Server** (`Product/api.py`): RESTful API for frontend communication
- **Julia Solver** (`Julia_Solver/course_scheduler.jl`): Core optimization engine using JuMP/Gurobi
- **Python Interface** (`Product/solver_interface.py`): Bridge between Python and Julia
- **Explanation Agent** (`Product/explanation_agent.py`): AI-powered explanation generation
- **Storage Layer** (`Product/storage.py`): SQLite database for run history

### Frontend
- **Vue.js Application** (`Product/University_Schedule_Optimizer.html`): Single-page web application
- **Responsive Design**: Works on desktop and tablet devices
- **Real-time Updates**: Live optimization status and results

### Data Processing
- **Input Generation** (`Data/generate_input.py`): Generate test datasets
- **Batch Profiler** (`Data/batch_profiler.py`): Batch processing and performance analysis

## Requirements

### Software Dependencies
- **Python 3.8+**
- **Julia 1.8+**
- **Gurobi Optimizer** (Academic license required)
- **Node.js** (for development, if needed)

### Python Packages
See `requirements.txt` for complete list:
- `fastapi>=0.109.0` - Web framework
- `uvicorn>=0.27.0` - ASGI server
- `julia>=0.6.1` - Python-Julia bridge
- `google-generativeai>=0.3.0` - Gemini AI API
- `gurobipy>=11.0.0` - Gurobi Python interface

### Julia Packages
- `JuMP.jl` - Mathematical optimization modeling
- `Gurobi.jl` - Gurobi optimizer interface
- `JSON.jl` - JSON parsing

## Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd Project
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install Julia and Packages
```bash
# Install Julia from https://julialang.org/downloads/
# Then install Julia packages:
julia -e 'using Pkg; Pkg.add(["JuMP", "Gurobi", "JSON"])'
```

### 4. Configure Gurobi License
Create a `Julia_Solver/gurobi.lic` file with your Gurobi WLS license:
```
WLSACCESSID=your-access-id
WLSSECRET=your-secret
LICENSEID=your-license-id
```

Alternatively, set environment variables:
```bash
export WLSACCESSID=your-access-id
export WLSSECRET=your-secret
export LICENSEID=your-license-id
```

### 5. Configure API Keys
Update `config.py` with your Gemini API key:
```python
GEMINI_API_KEY = "your-api-key"
```

### 6. Initialize PyJulia
```bash
python -c 'import julia; julia.install()'
```

## Usage

### Starting the Server

1. **Start the FastAPI backend:**
```bash
cd Product
uvicorn api:app --reload
```

The API will be available at `http://localhost:8000`

2. **Open the frontend:**
   - Open `Product/University_Schedule_Optimizer.html` in a web browser
   - Or serve it via a web server

### Using the Web Interface

1. **Input Configuration**: Enter course, instructor, room, and student data
2. **Run Optimization**: Click "Optimize" to generate a schedule
3. **View Results**: Explore the generated schedule in the calendar view
4. **Ask Questions**: Use the AI Assistant to get explanations
5. **What-If Analysis**: Explore alternative schedules with different constraints

### Command Line Interface

```bash
# Run optimization
python Product/main.py run --input Data/batch_output/schedule_input_001.json

# Explain a run
python Product/main.py explain --run-id run_20251206_123456

# Compare runs
python Product/main.py compare --run-id1 run_001 --run-id2 run_002

# List runs
python Product/main.py list --limit 10
```

### Batch Processing

```bash
# Generate test data
cd Data
python generate_input.py

# Run batch optimization
python batch_profiler.py
```

## Project Structure

```
Project/
├── Product/                      # Main application
│   ├── api.py                    # FastAPI server
│   ├── solver_interface.py       # Python-Julia bridge
│   ├── pipeline.py               # Main orchestration
│   ├── explanation_agent.py      # AI explanation generation
│   ├── query_translator.py        # What-if query translation
│   ├── storage.py                # Database layer
│   ├── University_Schedule_Optimizer.html  # Web frontend
│   └── scheduling.db             # SQLite database
│
├── Julia_Solver/                 # Optimization engine
│   ├── course_scheduler.jl       # Main MILP model
│   ├── what_if_analysis.jl      # What-if analysis
│   ├── cli_runner.jl             # Command-line interface
│   └── gurobi.lic               # Gurobi license (not in repo)
│
├── Data/                          # Data generation and batch processing
│   ├── generate_input.py        # Test data generator
│   ├── batch_profiler.py         # Batch processing
│   └── batch_output/            # Generated test files
│
├── Results/                       # Analysis results
│   └── tracking_logs/            # Optimization tracking data
│
├── config.py                     # Configuration
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## Configuration

### Environment Variables
- `SOLVER_TYPE`: Set to `"julia"` (default), `"python"`, or `"mock"`
- `WLSACCESSID`, `WLSSECRET`, `LICENSEID`: Gurobi license credentials

### Config File (`config.py`)
- `GEMINI_API_KEY`: Google Gemini API key for explanations
- `GEMINI_MODEL`: Model to use (default: `"gemini-2.5-flash"`)
- `STORAGE_DIR`: Directory for storing run data
- `MAX_EXPLANATION_TOKENS`: Maximum tokens for AI explanations

## Optimization Model

### Hard Constraints
- **C1**: Room capacity must accommodate enrolled students
- **C2**: Instructor availability (instructors can only teach when available)
- **C3**: Required sessions (courses must have specified number of sessions)
- **C4**: Session continuity (sessions must be continuous time blocks)
- **C5**: Room availability (rooms can only be used when available)

### Soft Constraints (Minimized)
- **S1**: Student conflicts (penalty for students with overlapping courses)
- **S2**: Instructor back-to-back preferences (reward for compact schedules)
- **S3**: Preferred time slots (penalty for scheduling outside preferred times)

### Objective Function
```
Minimize: w1 * S1 + w2 * S2 + w3 * S3
```
Where `w1`, `w2`, `w3` are configurable weights.

## Testing

### Generate Test Data
```bash
cd Data
python generate_input.py
```

### Run Single Test
```bash
python Product/main.py run --input Data/batch_output/schedule_input_001.json
```

### Batch Testing
```bash
cd Data
python batch_profiler.py
```

## API Documentation

### Endpoints

- `POST /optimize` - Run optimization
- `GET /runs` - List optimization runs
- `GET /runs/{run_id}` - Get run details
- `POST /explain` - Generate explanation
- `POST /what-if` - Run what-if analysis
- `GET /health/julia` - Check Julia runtime health

See API documentation at `http://localhost:8000/docs` when server is running.

## Troubleshooting

### Julia Runtime Errors
- **Access Violation**: Restart the Python server to reinitialize Julia
- **License Errors**: Verify Gurobi license is correctly configured
- **Import Errors**: Ensure Julia packages are installed: `julia -e 'using Pkg; Pkg.add(["JuMP", "Gurobi", "JSON"])'`

### Encoding Issues (Windows)
- The system handles UTF-8 encoding automatically
- If issues persist, ensure your terminal supports UTF-8

### Performance Issues
- Large problems may take several minutes
- Adjust `TimeLimit` in `course_scheduler.jl` for faster results
- Use batch processing for multiple runs

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is for academic use. Gurobi requires an academic license for use.

## Acknowledgments

- **Gurobi Optimization** for the optimization solver
- **JuMP.jl** for mathematical optimization modeling
- **Google Gemini** for AI-powered explanations
- **FastAPI** for the web framework
- **Vue.js** for the frontend framework
- **Prof. Dan Iancu** came up with this idea and supervised this project. Personal Website: https://web.stanford.edu/~daniancu/

## Contact

For questions or issues, please open an issue on GitHub.

---

**Note**: This project requires a Gurobi academic license. Ensure you have proper licensing before use.

import os
import glob
import subprocess
import re
import json
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime

# Set UTF-8 encoding for Windows compatibility
if os.name == 'nt':  # Windows
    import sys
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ================= CONFIGURATION =================
# Dynamically find paths relative to this script's location
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))  # This is Project/Data/
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)               # This is Project/

# Path to Julia Executable (ensure this is in your system PATH)
JULIA_EXECUTABLE = "julia" 

# Path to the Julia CLI Runner (Up one level -> Julia_Solver)
SOLVER_SCRIPT = os.path.join(PROJECT_ROOT, "Julia_Solver", "cli_runner.jl")

# Path to Inputs (Current Dir -> batch_output)
INPUT_DIR = os.path.join(CURRENT_DIR, "batch_output")

# Path to Results (Up one level -> Results)
TRACKING_DIR = os.path.join(PROJECT_ROOT, "Results", "tracking_logs")
PLOTS_DIR = os.path.join(PROJECT_ROOT, "Results", "plots")

# ================= REGEX PATTERNS =================
# Regex to capture Initial Score from Julia output
# Matches: "📊 Initial Heuristic Score: 1234.5 (S1: ... S2: ... S3: ...)"
REGEX_INITIAL_SCORE = re.compile(r"Initial Heuristic Score:\s*([\d\.]+)")

# Regex to capture Gurobi Progress
# Matches lines ending in 's' (seconds) with objective values
REGEX_GUROBI_LOG = re.compile(r"^\s*(H|\*)?\s*\d+\s+\d+.*?\s+([0-9\.]+)\s+[0-9\.]+\s+.*?\s+(\d+)s")

def ensure_dirs():
    os.makedirs(TRACKING_DIR, exist_ok=True)
    os.makedirs(PLOTS_DIR, exist_ok=True)

def parse_solver_output(process, filename):
    """
    Reads stdout line by line to capture optimization progress.
    """
    tracking_data = []
    initial_score = None
    
    print(f"   ... Running solver for {filename}")
    
    # Debug: print first few lines to see what we're getting
    debug_lines = []
    line_count = 0
    
    for line in iter(process.stdout.readline, ''):
        if not line:
            break
        line_count += 1
        if line_count <= 5:  # Print first 5 lines for debugging
            debug_lines.append(line.strip())
        
        line = line.strip()
        if not line:
            continue
            
        # 1. Capture Initial Score (Baseline)
        # Try both with and without emoji prefix
        init_match = REGEX_INITIAL_SCORE.search(line)
        if init_match:
            initial_score = float(init_match.group(1))
            print(f"      -> Detected Initial Score: {initial_score}")
            tracking_data.append({
                "time": 0,
                "objective": initial_score,
                "pct_reduced": 0.0
            })
            continue
        
        # Also check for the emoji version (📊)
        if "Initial Heuristic Score:" in line or "📊 Initial Heuristic Score:" in line:
            # Extract the number after the colon
            match = re.search(r"Initial Heuristic Score:\s*([\d\.]+)", line)
            if match:
                initial_score = float(match.group(1))
                print(f"      -> Detected Initial Score: {initial_score}")
                tracking_data.append({
                    "time": 0,
                    "objective": initial_score,
                    "pct_reduced": 0.0
                })
                continue

        # 2. Capture Gurobi Improvements
        if "Incumbent" in line and "Time" in line:
            continue 
            
        parts = line.split()
        # Basic heuristic to identify Gurobi log lines ending in 's'
        if len(parts) > 5 and parts[-1].endswith('s') and initial_score:
            try:
                time_str = parts[-1].replace('s', '')
                time_val = int(time_str)
                
                current_best = None
                
                # Heuristic: Find first float <= Initial Score that looks like an objective
                floats = []
                for p in parts:
                    try:
                        f = float(p)
                        floats.append(f)
                    except:
                        pass
                
                for f in floats:
                    if f <= initial_score and f > 0:
                        # Filter out small integers that might be node counts
                        if f > 100000 or f < initial_score - 1: 
                            current_best = f
                            break
                
                # Fallback for 'H' (Heuristic) lines
                if current_best is None and "H" in parts[0]:
                    current_best = float(parts[-2])
                
                if current_best is not None:
                    pct_reduced = ((initial_score - current_best) / initial_score) * 100
                    
                    if not tracking_data or tracking_data[-1]['time'] != time_val:
                        tracking_data.append({
                            "time": time_val,
                            "objective": current_best,
                            "pct_reduced": pct_reduced
                        })
            except Exception:
                pass 

    # Debug output if no data found
    if not tracking_data and debug_lines:
        print(f"      ⚠️ Debug: First 5 output lines:")
        for dl in debug_lines:
            print(f"         {dl}")
    
    return tracking_data, initial_score

def plot_progress(data, file_basename, initial_score):
    if not data:
        print(f"      ⚠️ No tracking data found for {file_basename}")
        return

    df = pd.DataFrame(data)
    
    plt.figure(figsize=(10, 6))
    plt.plot(df['time'], df['pct_reduced'], marker='o', linestyle='-', color='#2c3e50', linewidth=2)
    
    plt.title(f"Optimization Progress: {file_basename}\nInitial Penalty: {int(initial_score)}", fontsize=14)
    plt.xlabel("Time (seconds)", fontsize=12)
    plt.ylabel("Reduction in Penalty (%)", fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.ylim(bottom=0) 
    
    plt.fill_between(df['time'], df['pct_reduced'], color='#3498db', alpha=0.2)
    
    if not df.empty:
        final = df.iloc[-1]
        plt.annotate(f"{final['pct_reduced']:.1f}%", 
                     (final['time'], final['pct_reduced']),
                     xytext=(5, 5), textcoords='offset points')

    plot_path = os.path.join(PLOTS_DIR, f"{file_basename}.png")
    plt.savefig(plot_path)
    plt.close()
    print(f"      📸 Plot saved to {plot_path}")

def run_batch():
    ensure_dirs()
    
    input_files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    if not input_files:
        print(f"❌ No JSON files found in {INPUT_DIR}")
        print("   Please run generate_input.py first.")
        return

    print(f"Found {len(input_files)} input files in {INPUT_DIR}")
    print("="*60)

    # Setup Gurobi license environment variables before running
    gurobi_lic_file = os.path.join(PROJECT_ROOT, "Julia_Solver", "gurobi.lic")
    env = os.environ.copy()
    
    if os.path.exists(gurobi_lic_file):
        # Read license file and set environment variables
        with open(gurobi_lic_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    env[key] = value
                    env[f'GRB_{key}'] = value  # Gurobi also uses GRB_ prefix
        # Also set the license file path for Gurobi
        env['GRB_LICENSE_FILE'] = gurobi_lic_file
        print(f"🔑 Loaded Gurobi license from {gurobi_lic_file}")
    else:
        # Fallback to config.py values if license file not found
        try:
            sys.path.insert(0, PROJECT_ROOT)
            from config import Config
            env['WLSACCESSID'] = Config.GUROBI_WLS_LICENSE_ID
            env['GRB_WLSACCESSID'] = Config.GUROBI_WLS_LICENSE_ID
            env['LICENSEID'] = Config.GUROBI_WLS_LICENSE_ID
            env['GRB_LICENSEID'] = Config.GUROBI_WLS_LICENSE_ID
            print("⚠️ Using config.py license values (license file not found)")
        except:
            pass

    for i, input_path in enumerate(input_files):
        file_basename = os.path.splitext(os.path.basename(input_path))[0]
        print(f"[{i+1}/{len(input_files)}] Processing: {file_basename}")
        
        # Run from Julia_Solver directory so includes work correctly
        julia_solver_dir = os.path.join(PROJECT_ROOT, "Julia_Solver")
        solver_script_name = os.path.basename(SOLVER_SCRIPT)
        cmd = [JULIA_EXECUTABLE, solver_script_name, input_path]
        
        # Run Julia and capture output from the correct directory
        try:
            proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                text=True, 
                encoding='utf-8',
                errors='replace',  # Replace invalid UTF-8 sequences instead of failing
                bufsize=1,
                cwd=julia_solver_dir,  # Run from Julia_Solver directory
                env=env  # Pass environment variables with Gurobi license
            )
            tracking_data, initial_score = parse_solver_output(proc, file_basename)
            proc.wait()  # Wait for process to complete
        except Exception as e:
            print(f"      ❌ Error running solver: {e}")
            tracking_data = []
            initial_score = None
            
        if tracking_data:
            log_path = os.path.join(TRACKING_DIR, f"{file_basename}_log.json")
            with open(log_path, 'w') as f:
                json.dump({
                    "filename": file_basename,
                    "initial_score": initial_score,
                    "checkpoints": tracking_data
                }, f, indent=2)
            
            plot_progress(tracking_data, file_basename, initial_score if initial_score else 0)
        else:
            print("      ⚠️ Failed to extract tracking data. Check if Julia/Gurobi ran correctly.")
        
        print("-" * 60)

if __name__ == "__main__":
    run_batch()
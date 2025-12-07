import os
import json
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import interpolate

# ================= CONFIGURATION =================
CURRENT_DIR = os.getcwd()
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

TRACKING_DIR = os.path.join(PROJECT_ROOT, "Results", "tracking_logs")
PLOTS_DIR = os.path.join(PROJECT_ROOT, "Results", "plots")

FILE_START = 51
FILE_END = 100


def load_tracking_logs(tracking_dir, file_start, file_end):
    all_data = []
    for i in range(file_start, file_end + 1):
        filename = f"schedule_input_{i:03d}_log.json"
        filepath = os.path.join(tracking_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = json.load(f)
                data['file_id'] = i
                all_data.append(data)
                print(f"  ✓ Loaded: {filename}")
        else:
            print(f"  ⚠ Missing: {filename}")
    return all_data


def interpolate_to_common_times(all_data, num_points=100):
    if not all_data:
        return None, None, []
    
    max_times = []
    for data in all_data:
        if data['checkpoints']:
            max_times.append(max(cp['time'] for cp in data['checkpoints']))
    
    if not max_times:
        return None, None, []
    
    target_max_time = np.median(max_times)
    common_times = np.linspace(0, target_max_time, num_points)
    
    interpolated_values = []
    valid_runs = []
    
    for data in all_data:
        checkpoints = data['checkpoints']
        if len(checkpoints) < 2:
            continue
        
        times = np.array([cp['time'] for cp in checkpoints])
        pct_reduced = np.array([cp['pct_reduced'] for cp in checkpoints])
        
        sort_idx = np.argsort(times)
        times = times[sort_idx]
        pct_reduced = pct_reduced[sort_idx]
        
        unique_times, unique_idx = np.unique(times, return_index=True)
        times = unique_times
        pct_reduced = pct_reduced[unique_idx]
        
        if len(times) < 2:
            continue
        
        try:
            f = interpolate.interp1d(
                times, pct_reduced,
                kind='linear',
                bounds_error=False,
                fill_value=(pct_reduced[0], pct_reduced[-1])
            )
            interp_values = f(common_times)
            interpolated_values.append(interp_values)
            valid_runs.append(data['file_id'])
        except Exception as e:
            print(f"  ⚠ Error interpolating file {data['file_id']}: {e}")
    
    if not interpolated_values:
        return None, None, []
    
    return common_times, np.array(interpolated_values), valid_runs


def compute_statistics(interpolated_values):
    mean = np.mean(interpolated_values, axis=0)
    std = np.std(interpolated_values, axis=0)
    n = interpolated_values.shape[0]
    ci = 1.96 * std / np.sqrt(n)
    
    return {
        'mean': mean,
        'std': std,
        'ci_lower': mean - ci,
        'ci_upper': mean + ci,
        'median': np.median(interpolated_values, axis=0),
        'q25': np.percentile(interpolated_values, 25, axis=0),
        'q75': np.percentile(interpolated_values, 75, axis=0),
    }


def plot_combined(common_times, stats, num_runs, plots_dir):
    os.makedirs(plots_dir, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(14, 8))
    
    ax.fill_between(common_times, stats['q25'], stats['q75'],
                    alpha=0.25, color='#9b59b6', label='IQR (25th-75th)')
    
    ax.fill_between(common_times, stats['ci_lower'], stats['ci_upper'], 
                    alpha=0.4, color='#3498db', label='95% CI')
    
    ax.plot(common_times, stats['median'], linewidth=2, color='#27ae60',
            linestyle='--', label='Median')
    
    ax.plot(common_times, stats['mean'], linewidth=3, color='#2c3e50',
            label=f'Mean (n={num_runs})')
    
    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Reduction in Penalty (%)', fontsize=12)
    ax.set_title(f'Optimization Progress: Aggregate Results (Files {FILE_START:03d}-{FILE_END:03d})\n'
                 f'n={num_runs} runs', fontsize=14)
    ax.grid(True, linestyle='--', alpha=0.7)
    ax.legend(loc='lower right', fontsize=10)
    ax.set_ylim(bottom=0)
    
    final_mean = stats['mean'][-1]
    final_ci = (stats['ci_upper'][-1] - stats['ci_lower'][-1]) / 2
    ax.annotate(f'Final: {final_mean:.1f}% ± {final_ci:.1f}%',
                xy=(common_times[-1], final_mean),
                xytext=(-120, 20), textcoords='offset points', fontsize=11,
                arrowprops=dict(arrowstyle='->', color='gray'))
    
    plt.tight_layout()
    plot_path = os.path.join(plots_dir, 'aggregate_combined.png')
    plt.savefig(plot_path, dpi=150)
    plt.close()
    print(f"📊 Saved: {plot_path}")


def plot_all_runs(all_data, common_times, stats, plots_dir):
    fig, ax = plt.subplots(figsize=(14, 8))
    
    for data in all_data:
        checkpoints = data['checkpoints']
        if len(checkpoints) < 2:
            continue
        times = [cp['time'] for cp in checkpoints]
        pct_reduced = [cp['pct_reduced'] for cp in checkpoints]
        ax.plot(times, pct_reduced, alpha=0.3, linewidth=1, color='#3498db')
    
    ax.plot(common_times, stats['mean'], linewidth=3, color='#e74c3c',
            label=f'Mean (n={len(all_data)})')
    
    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Reduction in Penalty (%)', fontsize=12)
    ax.set_title(f'All Optimization Runs (Files {FILE_START:03d}-{FILE_END:03d})', fontsize=14)
    ax.grid(True, linestyle='--', alpha=0.7)
    ax.legend(loc='lower right', fontsize=10)
    ax.set_ylim(bottom=0)
    
    plt.tight_layout()
    plot_path = os.path.join(plots_dir, 'all_runs_overlay.png')
    plt.savefig(plot_path, dpi=150)
    plt.close()
    print(f"📊 Saved: {plot_path}")


def generate_summary_table(all_data, plots_dir):
    rows = []
    for data in all_data:
        checkpoints = data['checkpoints']
        if not checkpoints:
            continue
        initial_score = data.get('initial_score', checkpoints[0]['objective'])
        final_checkpoint = checkpoints[-1]
        rows.append({
            'file_id': data['file_id'],
            'filename': data['filename'],
            'initial_score': initial_score,
            'final_objective': final_checkpoint['objective'],
            'final_pct_reduced': final_checkpoint['pct_reduced'],
            'final_time': final_checkpoint['time'],
            'num_checkpoints': len(checkpoints)
        })
    
    df = pd.DataFrame(rows)
    csv_path = os.path.join(plots_dir, 'run_summary.csv')
    df.to_csv(csv_path, index=False)
    print(f"📄 Saved: {csv_path}")
    
    print("\n" + "="*60)
    print("SUMMARY STATISTICS")
    print("="*60)
    print(f"Total runs: {len(df)}")
    print(f"\nInitial Score:   Mean={df['initial_score'].mean():.1f}, Std={df['initial_score'].std():.1f}")
    print(f"Final Reduction: Mean={df['final_pct_reduced'].mean():.2f}%, Std={df['final_pct_reduced'].std():.2f}%")
    print(f"Solve Time:      Mean={df['final_time'].mean():.1f}s, Std={df['final_time'].std():.1f}s")
    print("="*60)
    
    return df


def main():
    print("="*60)
    print(f"AGGREGATE RESULTS: Files {FILE_START:03d}-{FILE_END:03d}")
    print("="*60)
    print(f"Current Dir:  {CURRENT_DIR}")
    print(f"Project Root: {PROJECT_ROOT}")
    print(f"Tracking Dir: {TRACKING_DIR}")
    print(f"Plots Dir:    {PLOTS_DIR}")
    
    if not os.path.exists(TRACKING_DIR):
        print(f"\n❌ Tracking directory not found: {TRACKING_DIR}")
        return
    
    os.makedirs(PLOTS_DIR, exist_ok=True)
    
    print(f"\n📂 Loading tracking logs...")
    all_data = load_tracking_logs(TRACKING_DIR, FILE_START, FILE_END)
    
    if not all_data:
        print("\n❌ No tracking logs found!")
        return
    
    print(f"\n✓ Loaded {len(all_data)} logs")
    
    print("\n📈 Interpolating...")
    common_times, interpolated_values, valid_runs = interpolate_to_common_times(all_data)
    
    if common_times is None:
        print("\n❌ Failed to interpolate!")
        return
    
    print(f"✓ Interpolated {len(valid_runs)} runs")
    
    print("\n📊 Computing statistics...")
    stats = compute_statistics(interpolated_values)
    
    print("\n🎨 Generating plots...")
    plot_combined(common_times, stats, len(valid_runs), PLOTS_DIR)
    plot_all_runs(all_data, common_times, stats, PLOTS_DIR)
    generate_summary_table(all_data, PLOTS_DIR)
    
    print(f"\n✅ Done! Results saved to: {PLOTS_DIR}")


if __name__ == "__main__":
    main()
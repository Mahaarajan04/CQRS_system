"""
consistency_plot.py — Parse CQRS summary logs and plot consistency lag vs N for different delays.

Expected log files:
    run_n100_d0.10.log
    run_n200_d0.05.log
    ...

Expected folder structure:
    scripts/
    ├── consistency_plot.py
    └── results/
        └── consistency_logs/
            ├── run_n100_d0.10.log
            ├── run_n200_d0.10.log
            ├── ...

What it does:
    - Extracts N and delay from the filename
    - Extracts p50 / p95 / p99 consistency lag from the summary section
    - Produces one plot per metric:
        lag_p50.png
        lag_p95.png
        lag_p99.png

Usage:
    python consistency_plot.py
    python consistency_plot.py --dir results/consistency_logs --out results/
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError:
    print("matplotlib not found. Install it with: pip install matplotlib")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# ANSI stripper
# ─────────────────────────────────────────────────────────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

# ─────────────────────────────────────────────────────────────────────────────
# Filename parser
# ─────────────────────────────────────────────────────────────────────────────
# Matches:
#     run_n100_d0.10.log
#     run_n500_d0.05.log
#     run_n1000_d0.01.log
FILE_RE = re.compile(r"^run_n(\d+)_d([\d.]+)\.log$")

def parse_filename(filename: str):
    """
    Returns (N, delay) or None.
    Example:
        run_n100_d0.10.log -> (100, 0.10)
    """
    m = FILE_RE.match(filename)
    if not m:
        return None
    N = int(m.group(1))
    delay = float(m.group(2))
    return N, delay

# ─────────────────────────────────────────────────────────────────────────────
# Summary parser
# ─────────────────────────────────────────────────────────────────────────────
# Handles summary blocks like:
#
#   consistency lag ms
#     p50    492.0
#     p95    549.6
#     p99    556.6
#
# The regex is intentionally flexible with whitespace and line breaks.
SUMMARY_RE = re.compile(
    r"consistency\s+lag\s+ms.*?"
    r"p50\s+([\d.]+).*?"
    r"p95\s+([\d.]+).*?"
    r"p99\s+([\d.]+)",
    re.IGNORECASE | re.DOTALL,
)

def parse_file(path: Path):
    """
    Returns (N, delay, (p50, p95, p99)) or None.
    """
    fname_parsed = parse_filename(path.name)
    if not fname_parsed:
        return None

    N, delay = fname_parsed
    text = strip_ansi(path.read_text(errors="replace"))

    m = SUMMARY_RE.search(text)
    if not m:
        print(f"  [warn] Lag not found in {path.name}")
        return None

    p50 = float(m.group(1))
    p95 = float(m.group(2))
    p99 = float(m.group(3))

    return N, delay, (p50, p95, p99)

# ─────────────────────────────────────────────────────────────────────────────
# Data loader
# ─────────────────────────────────────────────────────────────────────────────
def load_results(results_dir: Path):
    """
    Returns:
        data[delay][N] = (p50, p95, p99)
    """
    data = defaultdict(dict)
    found = 0

    for path in sorted(results_dir.rglob("*.log")):
        parsed = parse_file(path)
        if not parsed:
            print(f"  [warn] Could not parse {path.name}")
            continue

        N, delay, metrics = parsed
        data[delay][N] = metrics
        found += 1

        print(
            f"  [ok] {path.name:25s}  "
            f"delay={delay:<5g}  N={N:<5d}  "
            f"p50={metrics[0]:.1f}  p95={metrics[1]:.1f}  p99={metrics[2]:.1f}"
        )

    print(f"\nLoaded {found} files\n")
    return data

# ─────────────────────────────────────────────────────────────────────────────
# Plotting
# ─────────────────────────────────────────────────────────────────────────────
METRIC_COLORS = {
    "p50": "#4CAF50",
    "p95": "#FF9800",
    "p99": "#F44336",
}

def plot_metric(data, metric_idx: int, metric_name: str, out_dir: Path):
    """
    One plot per metric.
    X-axis: N
    Y-axis: lag
    One line per delay.
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    plotted = False

    for delay in sorted(data.keys()):
        n_map = data[delay]
        ns = sorted(n_map.keys())
        if not ns:
            continue

        values = [n_map[n][metric_idx] for n in ns]

        ax.plot(
            ns,
            values,
            marker="o",
            linewidth=2,
            markersize=6,
            label=f"delay={delay}s",
        )
        plotted = True

    if not plotted:
        print(f"  [warn] No data to plot for {metric_name}")
        plt.close(fig)
        return

    ax.set_title(f"Consistency Lag — {metric_name}", fontsize=14, fontweight="bold")
    ax.set_xlabel("N", fontsize=12)
    ax.set_ylabel(f"Lag ({metric_name}, ms)", fontsize=12)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(fontsize=10)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())

    fig.tight_layout()
    out_path = out_dir / f"lag_{metric_name}.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  [saved] {out_path}")

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Plot CQRS consistency lag results")
    parser.add_argument(
        "--dir",
        default=None,
        help="Directory containing result .log files (default: scripts/results/consistency_logs)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for plots (default: scripts/results)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    results_dir = Path(args.dir) if args.dir else script_dir / "results" / "consistency_logs"
    out_dir = Path(args.out) if args.out else script_dir / "results"

    if not results_dir.exists():
        print(f"Error: results directory '{results_dir}' not found.")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nScanning: {results_dir.resolve()}")
    print(f"Saving plots to:  {out_dir.resolve()}\n")

    data = load_results(results_dir)

    if not data:
        print("No valid files found.")
        sys.exit(1)

    for idx, metric_name in [(0, "p50"), (1, "p95"), (2, "p99")]:
        plot_metric(data, idx, metric_name, out_dir)

    print(f"\nDone — 3 plots saved to {out_dir.resolve()}")

if __name__ == "__main__":
    main()
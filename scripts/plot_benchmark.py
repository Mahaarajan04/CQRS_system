"""
plot_benchmark.py — Parse benchmark result files and plot RYW lag line graphs.

Parses files matching:
    cached_slowconsumer_{delay}_{N}.txt
    nocache_slowconsumer_{delay}_{N}.txt

Produces 5 graphs:
    ryw_lag_p50.png                  — all modes & delays, p50 only
    ryw_lag_p95.png                  — all modes & delays, p95 only
    ryw_lag_p99.png                  — all modes & delays, p99 only
    ryw_lag_cached_delay0_01.png     — cached only, delay=0.01, p50+p95+p99
    ryw_lag_nocache_delay0_01.png    — no-cache only, delay=0.01, p50+p95+p99

Usage:
    python scripts/plot_benchmark.py
    python scripts/plot_benchmark.py --dir results/master_20240101_120000
    python scripts/plot_benchmark.py --dir results/ --out plots/
"""

import argparse
import os
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

# ── ANSI stripper ─────────────────────────────────────────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

# ── Filename parser ────────────────────────────────────────────────────────────
# Matches: cached_slowconsumer_0.01_500.txt  or  nocache_slowconsumer_0_01_500.txt
FILE_RE = re.compile(
    r"^(cached|nocache)_slowconsumer_(\d+[_\.]\d+)_(\d+)\.txt$"
)

def parse_filename(filename: str):
    """Returns (cache_mode, delay_float, N_int) or None."""
    m = FILE_RE.match(filename)
    if not m:
        return None
    mode  = m.group(1)
    delay = float(m.group(2).replace("_", "."))
    n     = int(m.group(3))
    return mode, delay, n

# ── File content parser ────────────────────────────────────────────────────────
RYW_RE = re.compile(
    r"RYW lag ms\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
)

def parse_ryw(filepath: Path):
    """Returns (p50, p95, p99) from a benchmark result file, or None."""
    text = strip_ansi(filepath.read_text(errors="replace"))
    m = RYW_RE.search(text)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))

# ── Data loader ───────────────────────────────────────────────────────────────
def load_results(results_dir: Path):
    """
    Walks results_dir (recursively) and loads all matching files.
    Returns a dict: data[mode][delay][N] = (p50, p95, p99)
    """
    data = defaultdict(lambda: defaultdict(dict))
    found = 0

    for path in sorted(results_dir.rglob("*.txt")):
        parsed = parse_filename(path.name)
        if not parsed:
            continue
        mode, delay, n = parsed
        ryw = parse_ryw(path)
        if ryw is None:
            print(f"  [warn] Could not parse RYW lag from {path.name} — skipping")
            continue
        data[mode][delay][n] = ryw
        found += 1
        print(f"  [ok] {path.name:45s}  mode={mode:7s}  delay={delay}  N={n}  "
              f"p50={ryw[0]:.1f}  p95={ryw[1]:.1f}  p99={ryw[2]:.1f}")

    print(f"\n  Loaded {found} file(s) from {results_dir}\n")
    return data

# ── Plot 1: one metric across all modes & delays ──────────────────────────────
COLORS = {
    "cached":  ["#2196F3", "#1565C0", "#82B1FF"],
    "nocache": ["#F44336", "#B71C1C", "#FF8A80"],
}
LINE_STYLES = ["-", "--", "-."]

def plot_metric(data, metric_idx: int, metric_name: str, out_dir: Path):
    """One graph per metric (p50/p95/p99) with 6 lines: cached+nocache × 3 delays."""
    fig, ax = plt.subplots(figsize=(10, 6))
    plotted = False

    for mode in ("cached", "nocache"):
        if mode not in data:
            continue
        delays     = sorted(data[mode].keys())
        color_list = COLORS[mode]

        for i, delay in enumerate(delays):
            n_map  = data[mode][delay]
            ns     = sorted(n_map.keys())
            values = [n_map[n][metric_idx] for n in ns]
            if not ns:
                continue

            label = f"{'cached' if mode == 'cached' else 'no-cache'}  delay={delay}s"
            ax.plot(
                ns, values,
                marker="o",
                label=label,
                color=color_list[i % len(color_list)],
                linestyle=LINE_STYLES[i % len(LINE_STYLES)],
                linewidth=2,
                markersize=6,
            )
            plotted = True

    if not plotted:
        print(f"  [warn] No data to plot for {metric_name} — skipping")
        plt.close()
        return

    ax.set_title(f"RYW Lag — {metric_name}  (cached vs no-cache × delay)",
                 fontsize=14, fontweight="bold")
    ax.set_xlabel("N (number of orders)", fontsize=12)
    ax.set_ylabel(f"RYW lag {metric_name} (ms)", fontsize=12)
    ax.legend(fontsize=10, loc="upper left")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    fig.tight_layout()

    out_path = out_dir / f"ryw_lag_{metric_name.lower()}.png"
    fig.savefig(out_path, dpi=150)
    plt.close()
    print(f"  [saved] {out_path}")

# ── Plot 2: fixed delay, one graph per mode, p50+p95+p99 as lines ─────────────
METRIC_COLORS = {
    "p50": "#4CAF50",
    "p95": "#FF9800",
    "p99": "#F44336",
}

def plot_fixed_delay(data, delay: float, out_dir: Path):
    """2 graphs (cached + nocache) at a fixed delay, with p50/p95/p99 as 3 lines."""
    for mode in ("cached", "nocache"):
        if mode not in data or delay not in data[mode]:
            print(f"  [warn] No data for mode={mode} delay={delay} — skipping")
            continue

        n_map = data[mode][delay]
        ns    = sorted(n_map.keys())

        if not ns:
            print(f"  [warn] No N values for mode={mode} delay={delay} — skipping")
            continue

        fig, ax = plt.subplots(figsize=(10, 6))

        for metric_idx, metric_name in [(0, "p50"), (1, "p95"), (2, "p99")]:
            values = [n_map[n][metric_idx] for n in ns]
            ax.plot(
                ns, values,
                marker="o",
                label=metric_name,
                color=METRIC_COLORS[metric_name],
                linewidth=2,
                markersize=6,
            )

        label = "Cached" if mode == "cached" else "No-Cache"
        ax.set_title(f"RYW Lag — {label}  (delay={delay}s)",
                     fontsize=14, fontweight="bold")
        ax.set_xlabel("N (number of orders)", fontsize=12)
        ax.set_ylabel("RYW lag (ms)", fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
        fig.tight_layout()

        out_path = out_dir / f"ryw_lag_{mode}_delay{str(delay).replace('.', '_')}.png"
        fig.savefig(out_path, dpi=150)
        plt.close()
        print(f"  [saved] {out_path}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Plot benchmark RYW lag results")
    parser.add_argument("--dir", default=None,
                        help="Directory to search for result .txt files (default: results/ next to this script)")
    parser.add_argument("--out", default=None,
                        help="Output directory for plots (default: same as --dir)")
    args = parser.parse_args()

    script_dir  = Path(__file__).resolve().parent
    results_dir = Path(args.dir) if args.dir else script_dir / "results"
    out_dir     = Path(args.out) if args.out else results_dir

    if not results_dir.exists():
        print(f"Error: results directory '{results_dir}' not found.")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nScanning: {results_dir.resolve()}")
    print(f"Output:   {out_dir.resolve()}\n")

    data = load_results(results_dir)

    if not data:
        print("No matching files found. Files must be named:")
        print("  cached_slowconsumer_{delay}_{N}.txt")
        print("  nocache_slowconsumer_{delay}_{N}.txt")
        sys.exit(1)

    for metric_idx, metric_name in [(0, "p50"), (1, "p95"), (2, "p99")]:
        plot_metric(data, metric_idx, metric_name, out_dir)

    plot_fixed_delay(data, delay=0.01, out_dir=out_dir)

    print(f"\nDone — 5 plots saved to {out_dir.resolve()}")


if __name__ == "__main__":
    main()
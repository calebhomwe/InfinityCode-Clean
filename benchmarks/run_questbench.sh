#!/bin/bash
# Complete QuestBench execution script for Infinity Code

set -e

echo "Infinity Code QuestBench - Internal Benchmark System"
echo "========================================================"

pip install -q pathlib

mkdir -p infinity_sessions infinity_results

echo ""
echo "Running QuestBench demo..."
python questbench_demo.py

echo ""
echo "QuestBench complete!"
echo ""
echo "Next steps:"
echo "1. Open the generated HTML dashboard in your browser"
echo "2. Review insights and worst-performing sessions"
echo "3. Track metrics over time as you improve Infinity Code"
echo ""
echo "To score your own sessions:"
echo "  - Use SessionRecorder to capture Infinity Code telemetry"
echo "  - Run BenchmarkRunner to process batches"
echo "  - Generate DashboardGenerator for visual reports"

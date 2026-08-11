#!/usr/bin/env python3
"""
QuestBench Demo for Infinity Code - Complete end-to-end example
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
import random

sys.path.insert(0, str(Path(__file__).parent))

from questbench import (
    SessionRecorder,
    Turn,
    SessionOutcome,
    QualityScorer,
    BenchmarkRunner,
    DashboardGenerator
)


def create_sample_sessions(storage_dir: Path, count: int = 20):
    """Create sample Infinity Code sessions for demo"""
    recorder = SessionRecorder(storage_dir)

    for i in range(count):
        session_id = f"inf_{i:04d}"
        user_id = f"user_{random.randint(1, 100)}"

        quality = random.choice(["excellent", "good", "fair", "poor", "critical"])

        files_intended = [f"file{j}.py" for j in range(random.randint(1, 5))]

        recorder.start_session(
            session_id=session_id,
            user_id=user_id,
            goal=f"Fix bug #{i}",
            files_intended=files_intended,
            model="infinity-model-v1"
        )

        num_turns = {
            "excellent": random.randint(3, 5),
            "good": random.randint(5, 8),
            "fair": random.randint(8, 12),
            "poor": random.randint(12, 20),
            "critical": random.randint(15, 25)
        }[quality]

        for j in range(num_turns):
            turn = Turn(
                role="user" if j % 2 == 0 else "assistant",
                content=f"Turn {j} content",
                timestamp=datetime.utcnow() + timedelta(minutes=j*2),
                files_touched=files_intended[:1] if quality != "critical" else files_intended + [f"unintended{j}.py"],
                cascade_breaks=0 if quality != "critical" else random.randint(1, 3)
            )
            recorder.record_turn(turn)

        outcome = {
            "excellent": SessionOutcome.COMPLETED,
            "good": SessionOutcome.COMPLETED,
            "fair": SessionOutcome.PARTIAL,
            "poor": SessionOutcome.FAILED,
            "critical": SessionOutcome.ABANDONED
        }[quality]

        credits = {
            "excellent": random.randint(50, 80),
            "good": random.randint(80, 120),
            "fair": random.randint(120, 200),
            "poor": random.randint(200, 300),
            "critical": random.randint(300, 500)
        }[quality]

        rating = {
            "excellent": 5,
            "good": 4,
            "fair": 3,
            "poor": 2,
            "critical": 1
        }[quality]

        recorder.end_session(
            outcome=outcome,
            credits_used=credits,
            user_rating=rating,
            user_feedback=f"Quality: {quality}"
        )

        print(f"Created session {session_id} ({quality})")


def main():
    """Run complete QuestBench demo"""
    print("Infinity Code QuestBench Demo")
    print("=" * 60)

    sessions_dir = Path("./infinity_sessions")
    results_dir = Path("./infinity_results")

    sessions_dir.mkdir(exist_ok=True)
    results_dir.mkdir(exist_ok=True)

    print("\nStep 1: Creating sample sessions...")
    create_sample_sessions(sessions_dir, count=20)

    print("\nStep 2: Running benchmark...")
    runner = BenchmarkRunner(sessions_dir, results_dir)
    results = runner.run_benchmark("inf_*.json")

    print(f"\nAnalyzed {results['sessions_count']} sessions")
    print(f"Overall score: {results['summary']['averages']['overall_score']:.1f}")

    print("\nStep 3: Generating dashboard...")
    generator = DashboardGenerator(results_dir)

    results_files = sorted(results_dir.glob("benchmark_*.json"))
    if results_files:
        latest = results_files[-1]
        dashboard = generator.generate_dashboard(latest)
        print(f"Dashboard saved: {dashboard}")
        print(f"\nOpen {dashboard} in your browser to view results!")
    else:
        print("No results files found")


if __name__ == "__main__":
    main()

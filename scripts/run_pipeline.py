"""End-to-end local pipeline: generate -> load -> dbt -> ML -> suggestions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_settings
from generator import generate_all
from load_bronze import load_bronze
from ml.evaluate_ground_truth import evaluate
from ml.fraud import write_fraud_scores
from ml.habits import write_habit_insights
from ml.subscriptions import write_subscription_enrichment
from ml.suggestions import write_suggestions


def run_dbt(settings: dict) -> None:
    project_dir = ROOT / settings["dbt"]["project_dir"]
    profiles_dir = ROOT / settings["dbt"]["profiles_dir"]
    dbt_exe = ROOT / ".venv" / "Scripts" / "dbt.exe"
    dbt_bin = str(dbt_exe) if dbt_exe.exists() else "dbt"
    cmd = [
        dbt_bin,
        "build",
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(profiles_dir),
        "--target",
        settings["dbt"]["target"],
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def main() -> None:
    settings = load_settings()
    print("=== 1/6 Generate synthetic data ===")
    paths = generate_all(settings)
    for k, v in paths.items():
        print(f"  {k}: {v}")

    print("\n=== 2/6 Load bronze into DuckDB ===")
    db_path = load_bronze(settings)
    print(f"  duckdb: {db_path}")

    print("\n=== 3/6 dbt build ===")
    run_dbt(settings)

    print("\n=== 4/6 Subscriptions + gold exports ===")
    write_subscription_enrichment(db_path, settings["paths"]["gold"])

    print("\n=== 5/6 Habits + fraud scoring ===")
    write_habit_insights(
        db_path,
        settings["paths"]["gold"],
        lookback_days=settings["ml"]["habit_lookback_days"],
    )
    write_fraud_scores(
        db_path,
        settings["paths"]["gold"],
        settings["paths"]["ml_artifacts"],
        contamination=settings["ml"]["fraud_contamination"],
    )

    print("\n=== 6/6 Suggestions + ground-truth recovery ===")
    write_suggestions(db_path, settings["paths"]["gold"])
    report = evaluate(
        db_path,
        settings["paths"]["ground_truth"],
        settings["paths"]["ml_artifacts"],
    )
    print("Ground-truth recovery:")
    for k, v in report.items():
        print(f"  {k}: {v}")

    print("\nPipeline complete.")
    print("Launch UI:  streamlit run app/streamlit_app.py")


if __name__ == "__main__":
    main()
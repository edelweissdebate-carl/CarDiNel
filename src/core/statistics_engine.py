from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import friedmanchisquare, mannwhitneyu, t as student_t, wilcoxon

from src.core.paths import (
    RESULTS_DIR,
    STATISTICS_DIR,
    ensure_app_directories,
    get_batch_result_file,
    get_satellite_statistics_dir,
)
from src.core.satellites import get_satellite


THRESHOLDS_KM = (1.0, 2.0, 5.0, 10.0)

ERROR_METRIC_COLUMNS = {
    "initial_error_km": "Initial error",
    "final_error_km": "Final error",
    "mean_error_km": "Mean error",
    "maximum_error_km": "Maximum error",
    "rms_error_km": "RMS error",
    "radial_rms_km": "Radial RMS",
    "along_track_rms_km": "Along-track RMS",
    "cross_track_rms_km": "Cross-track RMS",
}


@dataclass
class MetricSummary:
    count: int
    mean: float
    median: float
    standard_deviation: float
    minimum: float
    maximum: float
    confidence_interval_95_low: float
    confidence_interval_95_high: float


@dataclass
class HorizonSummary:
    threshold_km: float
    total_tles: int
    exact_crossings: int
    lower_bounds: int
    crossing_percentage: float
    exact_horizon_statistics: MetricSummary | None


def threshold_label(threshold_km: float) -> str:
    """Match the batch CSV convention: 1.0 -> '1', 2.0 -> '2'."""
    return f"{threshold_km:g}".replace(".", "_")


def summarize_values(values: pd.Series) -> MetricSummary:
    """Return descriptive statistics with a Student-t 95% CI for the mean."""
    clean = pd.to_numeric(values, errors="coerce").dropna()

    if clean.empty:
        raise ValueError("No valid numerical values were available.")

    count = int(len(clean))
    mean = float(clean.mean())
    median = float(clean.median())

    if count > 1:
        standard_deviation = float(clean.std(ddof=1))
        standard_error = standard_deviation / np.sqrt(count)
        critical_value = float(student_t.ppf(0.975, df=count - 1))
        margin = critical_value * standard_error
    else:
        standard_deviation = 0.0
        margin = 0.0

    return MetricSummary(
        count=count,
        mean=mean,
        median=median,
        standard_deviation=standard_deviation,
        minimum=float(clean.min()),
        maximum=float(clean.max()),
        confidence_interval_95_low=mean - margin,
        confidence_interval_95_high=mean + margin,
    )


def parse_boolean_series(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
    )


def get_exact_and_lower_masks(
    dataframe: pd.DataFrame,
    threshold_km: float,
) -> tuple[pd.Series, pd.Series]:
    """
    Prefer horizon_*_status. Fall back to crossed_* for compatibility
    with older CSVs.
    """
    label = threshold_label(threshold_km)
    status_column = f"horizon_{label}km_status"
    crossed_column = f"crossed_{label}km"

    if status_column in dataframe.columns:
        status = dataframe[status_column].astype(str).str.strip().str.lower()
        exact = status.eq("exact")
        lower = status.eq("lower_bound")

        invalid = ~(exact | lower)
        if invalid.any():
            bad = sorted(set(status.loc[invalid].tolist()))
            raise ValueError(f"Invalid values in {status_column}: {bad}")

        return exact, lower

    if crossed_column in dataframe.columns:
        crossed = parse_boolean_series(dataframe[crossed_column])
        if crossed.isna().any():
            raise ValueError(f"Could not parse every value in {crossed_column}.")
        return crossed == True, crossed == False

    raise ValueError(
        f"Missing reliability-status columns for the {threshold_km:g} km threshold."
    )


def summarize_horizon(
    dataframe: pd.DataFrame,
    threshold_km: float,
) -> HorizonSummary:
    label = threshold_label(threshold_km)
    horizon_column = f"horizon_{label}km_hours"

    if horizon_column not in dataframe.columns:
        raise ValueError(f"Missing CSV column: {horizon_column}")

    exact, lower = get_exact_and_lower_masks(dataframe, threshold_km)

    exact_values = pd.to_numeric(
        dataframe.loc[exact, horizon_column],
        errors="coerce",
    ).dropna()

    exact_crossings = int(exact.sum())
    lower_bounds = int(lower.sum())
    total_tles = int(len(dataframe))
    crossing_percentage = (
        (exact_crossings / total_tles) * 100.0
        if total_tles
        else 0.0
    )

    exact_statistics = (
        summarize_values(exact_values)
        if not exact_values.empty
        else None
    )

    return HorizonSummary(
        threshold_km=threshold_km,
        total_tles=total_tles,
        exact_crossings=exact_crossings,
        lower_bounds=lower_bounds,
        crossing_percentage=crossing_percentage,
        exact_horizon_statistics=exact_statistics,
    )


def validate_dataframe(dataframe: pd.DataFrame) -> None:
    required = {"satellite_name", "tle_epoch_utc", "rms_error_km"}
    missing = sorted(required - set(dataframe.columns))

    if missing:
        raise ValueError(
            f"Batch reliability CSV is missing required columns: {missing}"
        )

    if dataframe.empty:
        raise ValueError("The batch reliability CSV contains no rows.")


def load_analysis_dataframe(scope: str) -> tuple[pd.DataFrame, Path]:
    """
    Load one satellite result file or combine Sentinel-1A and Sentinel-1B.
    """
    if scope != "combined":
        satellite = get_satellite(scope)
        input_file = get_batch_result_file(satellite.key)

        if not input_file.exists():
            raise FileNotFoundError(
                f"Batch reliability CSV not found:\n{input_file}"
            )

        dataframe = pd.read_csv(input_file)
        validate_dataframe(dataframe)
        return dataframe, get_satellite_statistics_dir(satellite.key)

    frames: list[pd.DataFrame] = []
    missing_files: list[Path] = []

    for satellite_key in ("sentinel1a", "sentinel1b"):
        input_file = get_batch_result_file(satellite_key)

        if not input_file.exists():
            missing_files.append(input_file)
            continue

        frame = pd.read_csv(input_file)
        validate_dataframe(frame)
        frames.append(frame)

    if missing_files:
        joined = "\n".join(str(path) for path in missing_files)
        raise FileNotFoundError(
            "Combined analysis requires both satellite result files. "
            f"Missing:\n{joined}"
        )

    dataframe = pd.concat(frames, ignore_index=True)
    validate_dataframe(dataframe)

    combined_file = RESULTS_DIR / "combined_batch_reliability.csv"
    dataframe.to_csv(combined_file, index=False)

    return dataframe, STATISTICS_DIR / "combined"


def save_error_metric_summary(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> dict[str, MetricSummary]:
    summaries: dict[str, MetricSummary] = {}
    rows: list[dict[str, object]] = []

    for column, display_name in ERROR_METRIC_COLUMNS.items():
        if column not in dataframe.columns:
            continue

        summary = summarize_values(dataframe[column])
        summaries[column] = summary
        rows.append({"metric": display_name, **asdict(summary)})

    pd.DataFrame(rows).to_csv(
        output_folder / "error_metric_summary.csv",
        index=False,
    )
    return summaries


def save_error_metric_summary_by_satellite(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    rows: list[dict[str, object]] = []

    for satellite_name, group in dataframe.groupby("satellite_name", sort=True):
        for column, display_name in ERROR_METRIC_COLUMNS.items():
            if column not in group.columns:
                continue

            summary = summarize_values(group[column])
            rows.append(
                {
                    "satellite_name": satellite_name,
                    "metric": display_name,
                    **asdict(summary),
                }
            )

    if rows:
        pd.DataFrame(rows).to_csv(
            output_folder / "error_metric_summary_by_satellite.csv",
            index=False,
        )


def save_horizon_summary(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> list[HorizonSummary]:
    summaries = [
        summarize_horizon(dataframe, threshold)
        for threshold in THRESHOLDS_KM
    ]

    rows: list[dict[str, object]] = []

    for summary in summaries:
        row: dict[str, object] = {
            "threshold_km": summary.threshold_km,
            "total_tles": summary.total_tles,
            "exact_crossings": summary.exact_crossings,
            "lower_bounds": summary.lower_bounds,
            "crossing_percentage": summary.crossing_percentage,
        }

        if summary.exact_horizon_statistics is not None:
            row.update(
                {
                    f"exact_{key}": value
                    for key, value in asdict(
                        summary.exact_horizon_statistics
                    ).items()
                }
            )

        rows.append(row)

    pd.DataFrame(rows).to_csv(
        output_folder / "reliability_horizon_summary.csv",
        index=False,
    )
    return summaries


def save_horizon_summary_by_satellite(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    rows: list[dict[str, object]] = []

    for satellite_name, group in dataframe.groupby("satellite_name", sort=True):
        for threshold in THRESHOLDS_KM:
            summary = summarize_horizon(group, threshold)
            row: dict[str, object] = {
                "satellite_name": satellite_name,
                "threshold_km": threshold,
                "total_tles": summary.total_tles,
                "exact_crossings": summary.exact_crossings,
                "lower_bounds": summary.lower_bounds,
                "crossing_percentage": summary.crossing_percentage,
            }

            if summary.exact_horizon_statistics is not None:
                row.update(
                    {
                        f"exact_{key}": value
                        for key, value in asdict(
                            summary.exact_horizon_statistics
                        ).items()
                    }
                )

            rows.append(row)

    if rows:
        pd.DataFrame(rows).to_csv(
            output_folder / "reliability_horizon_summary_by_satellite.csv",
            index=False,
        )


def save_error_graphs(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    basic = [
        column
        for column in (
            "initial_error_km",
            "final_error_km",
            "mean_error_km",
            "maximum_error_km",
            "rms_error_km",
        )
        if column in dataframe.columns
    ]

    if basic:
        dataframe[basic].plot(kind="box", figsize=(10, 6))
        plt.title("Distribution of TLE/SGP4 position-error metrics")
        plt.ylabel("Position error (km)")
        plt.xlabel("Error metric")
        plt.xticks(rotation=20)
        plt.tight_layout()
        plt.savefig(output_folder / "error_metrics_boxplot.png", dpi=300)
        plt.close()

    components = [
        column
        for column in (
            "radial_rms_km",
            "along_track_rms_km",
            "cross_track_rms_km",
        )
        if column in dataframe.columns
    ]

    if components:
        dataframe[components].plot(kind="box", figsize=(9, 6))
        plt.title("RMS position-error components")
        plt.ylabel("RMS error (km)")
        plt.xlabel("Error component")
        plt.xticks(rotation=15)
        plt.tight_layout()
        plt.savefig(output_folder / "component_rms_boxplot.png", dpi=300)
        plt.close()

    if "rms_error_km" in dataframe.columns:
        values = pd.to_numeric(
            dataframe["rms_error_km"],
            errors="coerce",
        ).dropna()

        if not values.empty:
            plt.figure(figsize=(9, 6))
            plt.hist(values, bins="auto", edgecolor="black")
            plt.title("Distribution of 24-hour RMS position error")
            plt.xlabel("RMS position error (km)")
            plt.ylabel("Number of TLEs")
            plt.tight_layout()
            plt.savefig(output_folder / "rms_error_histogram.png", dpi=300)
            plt.close()


def save_horizon_graphs(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    grouped_values: list[np.ndarray] = []
    labels: list[str] = []

    for threshold in THRESHOLDS_KM:
        label = threshold_label(threshold)
        horizon_column = f"horizon_{label}km_hours"

        if horizon_column not in dataframe.columns:
            continue

        exact, _ = get_exact_and_lower_masks(dataframe, threshold)
        values = pd.to_numeric(
            dataframe.loc[exact, horizon_column],
            errors="coerce",
        ).dropna()

        if values.empty:
            continue

        grouped_values.append(values.to_numpy())
        labels.append(f"{threshold:g} km")

    if not grouped_values:
        return

    plt.figure(figsize=(9, 6))
    plt.boxplot(grouped_values, tick_labels=labels)
    plt.title("Exact reliability-horizon distributions")
    plt.xlabel("Position-error threshold")
    plt.ylabel("Reliability horizon (hours)")
    plt.tight_layout()
    plt.savefig(
        output_folder / "reliability_horizon_boxplot.png",
        dpi=300,
    )
    plt.close()


def save_epoch_trend(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    if (
        "tle_epoch_utc" not in dataframe.columns
        or "rms_error_km" not in dataframe.columns
    ):
        return

    plot_data = dataframe.copy()
    plot_data["tle_epoch_utc"] = pd.to_datetime(
        plot_data["tle_epoch_utc"],
        utc=True,
        errors="coerce",
    )
    plot_data["rms_error_km"] = pd.to_numeric(
        plot_data["rms_error_km"],
        errors="coerce",
    )
    plot_data = (
        plot_data.dropna(subset=["tle_epoch_utc", "rms_error_km"])
        .sort_values("tle_epoch_utc")
    )

    if plot_data.empty:
        return

    plt.figure(figsize=(10, 6))

    for satellite_name, group in plot_data.groupby(
        "satellite_name",
        sort=True,
    ):
        plt.plot(
            group["tle_epoch_utc"],
            group["rms_error_km"],
            marker="o",
            label=satellite_name,
        )

    plt.title("RMS prediction error by TLE epoch")
    plt.xlabel("TLE epoch")
    plt.ylabel("24-hour RMS position error (km)")

    if plot_data["satellite_name"].nunique() > 1:
        plt.legend()

    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(output_folder / "rms_error_by_epoch.png", dpi=300)
    plt.close()


def save_satellite_comparison_graphs(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    if dataframe["satellite_name"].nunique() < 2:
        return

    labels: list[str] = []
    groups: list[np.ndarray] = []

    for satellite_name, group in dataframe.groupby(
        "satellite_name",
        sort=True,
    ):
        values = pd.to_numeric(
            group["rms_error_km"],
            errors="coerce",
        ).dropna()

        if not values.empty:
            labels.append(satellite_name)
            groups.append(values.to_numpy())

    if len(groups) >= 2:
        plt.figure(figsize=(8, 6))
        plt.boxplot(groups, tick_labels=labels)
        plt.title("24-hour RMS position error by satellite")
        plt.xlabel("Satellite")
        plt.ylabel("RMS position error (km)")
        plt.tight_layout()
        plt.savefig(output_folder / "rms_error_by_satellite.png", dpi=300)
        plt.close()

    crossing_rows: list[dict[str, object]] = []

    for satellite_name, group in dataframe.groupby(
        "satellite_name",
        sort=True,
    ):
        for threshold in THRESHOLDS_KM:
            summary = summarize_horizon(group, threshold)
            crossing_rows.append(
                {
                    "satellite_name": satellite_name,
                    "threshold": f"{threshold:g} km",
                    "crossing_percentage": summary.crossing_percentage,
                }
            )

    crossing_dataframe = pd.DataFrame(crossing_rows)
    if crossing_dataframe.empty:
        return

    pivot = crossing_dataframe.pivot(
        index="threshold",
        columns="satellite_name",
        values="crossing_percentage",
    )
    pivot.plot(kind="bar", figsize=(9, 6))
    plt.title("Threshold-crossing percentage by satellite")
    plt.xlabel("Position-error threshold")
    plt.ylabel("TLEs crossing within 24 h (%)")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(
        output_folder / "threshold_crossing_by_satellite.png",
        dpi=300,
    )
    plt.close()


def find_extreme_tles(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    if "rms_error_km" not in dataframe.columns:
        return

    valid = dataframe.copy()
    valid["rms_error_km"] = pd.to_numeric(
        valid["rms_error_km"],
        errors="coerce",
    )
    valid = valid.dropna(subset=["rms_error_km"])

    if valid.empty:
        return

    best = valid.nsmallest(3, "rms_error_km")
    worst = valid.nlargest(3, "rms_error_km")
    extremes = pd.concat(
        [
            best.assign(category="Lowest RMS error"),
            worst.assign(category="Highest RMS error"),
        ],
        ignore_index=True,
    )

    selected_columns = [
        column
        for column in (
            "category",
            "satellite_name",
            "mission_code",
            "norad_id",
            "tle_epoch_utc",
            "initial_error_km",
            "final_error_km",
            "mean_error_km",
            "maximum_error_km",
            "rms_error_km",
            "radial_rms_km",
            "along_track_rms_km",
            "cross_track_rms_km",
        )
        if column in extremes.columns
    ]

    extremes[selected_columns].to_csv(
        output_folder / "best_and_worst_tles.csv",
        index=False,
    )


def run_friedman_component_tests(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> list[dict[str, object]]:
    """
    Compare radial, along-track, and cross-track RMS errors within
    the same TLE cases. This directly supports the component-error
    null hypothesis.
    """
    required = [
        "radial_rms_km",
        "along_track_rms_km",
        "cross_track_rms_km",
    ]

    if not all(column in dataframe.columns for column in required):
        return []

    rows: list[dict[str, object]] = []

    for satellite_name, group in dataframe.groupby(
        "satellite_name",
        sort=True,
    ):
        paired = group[required].apply(
            pd.to_numeric,
            errors="coerce",
        ).dropna()

        if len(paired) < 3:
            continue

        statistic, p_value = friedmanchisquare(
            paired["radial_rms_km"],
            paired["along_track_rms_km"],
            paired["cross_track_rms_km"],
        )

        rows.append(
            {
                "satellite_name": satellite_name,
                "test": "Friedman test",
                "comparison": "radial vs along-track vs cross-track RMS",
                "n_tles": int(len(paired)),
                "statistic": float(statistic),
                "p_value": float(p_value),
                "bonferroni_adjusted_p": None,
                "alpha": 0.05,
                "significant": bool(p_value < 0.05),
            }
        )

        if p_value < 0.05:
            pairs = [
                ("radial_rms_km", "along_track_rms_km"),
                ("radial_rms_km", "cross_track_rms_km"),
                ("along_track_rms_km", "cross_track_rms_km"),
            ]

            for first, second in pairs:
                try:
                    pair_statistic, pair_p = wilcoxon(
                        paired[first],
                        paired[second],
                    )
                except ValueError:
                    pair_statistic = np.nan
                    pair_p = 1.0

                adjusted_p = min(float(pair_p) * len(pairs), 1.0)

                rows.append(
                    {
                        "satellite_name": satellite_name,
                        "test": "Wilcoxon signed-rank",
                        "comparison": f"{first} vs {second}",
                        "n_tles": int(len(paired)),
                        "statistic": (
                            float(pair_statistic)
                            if not np.isnan(pair_statistic)
                            else None
                        ),
                        "p_value": float(pair_p),
                        "bonferroni_adjusted_p": adjusted_p,
                        "alpha": 0.05,
                        "significant": bool(adjusted_p < 0.05),
                    }
                )

    if rows:
        pd.DataFrame(rows).to_csv(
            output_folder / "component_error_inferential_tests.csv",
            index=False,
        )

    return rows


def run_satellite_rms_comparison(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> dict[str, object] | None:
    """
    Compare Sentinel-1A and Sentinel-1B total RMS error with a
    two-sided Mann-Whitney U test.
    """
    groups: dict[str, pd.Series] = {}

    for satellite_name, group in dataframe.groupby(
        "satellite_name",
        sort=True,
    ):
        values = pd.to_numeric(
            group["rms_error_km"],
            errors="coerce",
        ).dropna()

        if not values.empty:
            groups[satellite_name] = values

    if len(groups) != 2:
        return None

    names = sorted(groups)
    first_name, second_name = names

    statistic, p_value = mannwhitneyu(
        groups[first_name],
        groups[second_name],
        alternative="two-sided",
    )

    result = {
        "test": "Mann-Whitney U",
        "metric": "rms_error_km",
        "group_1": first_name,
        "group_1_n": int(len(groups[first_name])),
        "group_1_median": float(groups[first_name].median()),
        "group_2": second_name,
        "group_2_n": int(len(groups[second_name])),
        "group_2_median": float(groups[second_name].median()),
        "statistic": float(statistic),
        "p_value": float(p_value),
        "alpha": 0.05,
        "significant": bool(p_value < 0.05),
    }

    pd.DataFrame([result]).to_csv(
        output_folder / "satellite_rms_comparison.csv",
        index=False,
    )

    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate ORBIT-TRUST statistics for Sentinel-1A, "
            "Sentinel-1B, or a combined two-satellite dataset."
        )
    )
    parser.add_argument(
        "--satellite",
        choices=["sentinel1a", "sentinel1b", "combined"],
        default="combined",
        help="Analysis scope. Default: combined",
    )
    return parser.parse_args()


def generate_statistics(scope: str) -> Path:
    """
    Generate descriptive statistics, reliability summaries, figures,
    and the inferential tests supported by the batch-level dataset.
    """
    ensure_app_directories()
    dataframe, output_folder = load_analysis_dataframe(scope)
    output_folder.mkdir(parents=True, exist_ok=True)

    error_summaries = save_error_metric_summary(
        dataframe,
        output_folder,
    )
    save_error_metric_summary_by_satellite(
        dataframe,
        output_folder,
    )

    horizon_summaries = save_horizon_summary(
        dataframe,
        output_folder,
    )
    save_horizon_summary_by_satellite(
        dataframe,
        output_folder,
    )

    save_error_graphs(dataframe, output_folder)
    save_horizon_graphs(dataframe, output_folder)
    save_epoch_trend(dataframe, output_folder)
    save_satellite_comparison_graphs(dataframe, output_folder)
    find_extreme_tles(dataframe, output_folder)

    component_tests = run_friedman_component_tests(
        dataframe,
        output_folder,
    )
    satellite_test = run_satellite_rms_comparison(
        dataframe,
        output_folder,
    )

    satellite_counts = {
        str(name): int(count)
        for name, count in dataframe["satellite_name"].value_counts().items()
    }

    complete_summary = {
        "analysis_scope": scope,
        "tle_count": int(len(dataframe)),
        "satellite_counts": satellite_counts,
        "error_metrics": {
            name: asdict(summary)
            for name, summary in error_summaries.items()
        },
        "reliability_horizons": [
            {
                "threshold_km": summary.threshold_km,
                "total_tles": summary.total_tles,
                "exact_crossings": summary.exact_crossings,
                "lower_bounds": summary.lower_bounds,
                "crossing_percentage": summary.crossing_percentage,
                "exact_horizon_statistics": (
                    asdict(summary.exact_horizon_statistics)
                    if summary.exact_horizon_statistics is not None
                    else None
                ),
            }
            for summary in horizon_summaries
        ],
        "component_error_tests": component_tests,
        "satellite_rms_comparison": satellite_test,
    }

    summary_file = output_folder / "statistics_summary.json"
    with summary_file.open("w", encoding="utf-8") as json_file:
        json.dump(complete_summary, json_file, indent=2)

    print("=" * 70)
    print("ORBIT-TRUST Statistics")
    print("=" * 70)
    print(f"Analysis scope: {scope}")
    print(f"TLEs analyzed: {len(dataframe)}")

    for satellite_name, count in satellite_counts.items():
        print(f"  {satellite_name}: {count}")

    rms_summary = error_summaries.get("rms_error_km")
    if rms_summary is not None:
        print()
        print("Overall RMS error:")
        print(f"  Mean: {rms_summary.mean:.3f} km")
        print(f"  Median: {rms_summary.median:.3f} km")
        print(f"  SD: {rms_summary.standard_deviation:.3f} km")
        print(
            "  95% CI for mean: "
            f"{rms_summary.confidence_interval_95_low:.3f} to "
            f"{rms_summary.confidence_interval_95_high:.3f} km"
        )

    print()
    print("Reliability horizons:")

    for summary in horizon_summaries:
        print(
            f"  {summary.threshold_km:g} km: "
            f"{summary.exact_crossings} exact, "
            f"{summary.lower_bounds} lower-bound "
            f"({summary.crossing_percentage:.1f}% crossed)"
        )

        if summary.exact_horizon_statistics is not None:
            print(
                f"    Exact median: "
                f"{summary.exact_horizon_statistics.median:.3f} h"
            )

    if satellite_test is not None:
        print()
        print("Sentinel-1A vs Sentinel-1B RMS comparison:")
        print(f"  Mann-Whitney U = {satellite_test['statistic']:.3f}")
        print(f"  p = {satellite_test['p_value']:.6f}")

    print()
    print(f"Statistics saved to:\n{output_folder}")
    print("=" * 70)

    return output_folder


def main() -> None:
    args = parse_arguments()
    generate_statistics(scope=args.satellite)


if __name__ == "__main__":
    main()
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
from scipy.stats import spearmanr, wilcoxon

from src.core.paths import (
    RESULTS_DIR,
    STATISTICS_DIR,
    ensure_app_directories,
)
from src.core.satellites import (
    SATELLITES,
    get_satellite,
)


# ---------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------

@dataclass
class TLECorrelationResult:
    satellite_name: str
    mission_code: str
    norad_id: int
    tle_name: str
    tle_epoch_utc: str
    observation_count: int
    spearman_rho: float
    spearman_p_value: float


@dataclass
class CorrelationSummary:
    scope: str
    tle_count: int
    mean_rho: float
    median_rho: float
    standard_deviation_rho: float
    minimum_rho: float
    maximum_rho: float
    positive_correlations: int
    negative_correlations: int
    zero_correlations: int
    wilcoxon_statistic: float | None
    wilcoxon_p_value: float | None
    significance_level: float
    reject_null_hypothesis: bool | None


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

def get_timeseries_file(
    satellite_key: str,
) -> Path:
    satellite = get_satellite(
        satellite_key
    )

    return (
        RESULTS_DIR
        / f"{satellite.key}_error_timeseries.csv"
    )


def get_output_folder(
    scope: str,
) -> Path:
    return (
        STATISTICS_DIR
        / scope
        / "time_error"
    )


# ---------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------

def load_timeseries(
    scope: str,
) -> pd.DataFrame:
    """
    Load one or both satellite time-series files.
    """

    if scope == "combined":
        frames: list[pd.DataFrame] = []

        for satellite_key in (
            "sentinel1a",
            "sentinel1b",
        ):
            input_file = get_timeseries_file(
                satellite_key
            )

            if not input_file.exists():
                raise FileNotFoundError(
                    "Time-series CSV was not found:\n"
                    f"{input_file}"
                )

            frames.append(
                pd.read_csv(
                    input_file
                )
            )

        dataframe = pd.concat(
            frames,
            ignore_index=True,
        )

    else:
        input_file = get_timeseries_file(
            scope
        )

        if not input_file.exists():
            raise FileNotFoundError(
                "Time-series CSV was not found:\n"
                f"{input_file}"
            )

        dataframe = pd.read_csv(
            input_file
        )

    if dataframe.empty:
        raise ValueError(
            "The time-series dataset contains no rows."
        )

    required_columns = [
        "satellite_name",
        "mission_code",
        "norad_id",
        "tle_name",
        "tle_epoch_utc",
        "elapsed_hours",
        "total_error_km",
    ]

    missing = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing:
        raise ValueError(
            "Missing required time-series columns: "
            + ", ".join(missing)
        )

    return dataframe


# ---------------------------------------------------------------------
# Per-TLE Spearman analysis
# ---------------------------------------------------------------------

def calculate_tle_correlations(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate one Spearman correlation coefficient per historical TLE.

    This prevents the thousands of minute-level observations from
    being incorrectly treated as thousands of independent experiments.
    """

    results: list[
        TLECorrelationResult
    ] = []

    group_columns = [
        "satellite_name",
        "mission_code",
        "norad_id",
        "tle_name",
        "tle_epoch_utc",
    ]

    grouped = dataframe.groupby(
        group_columns,
        dropna=False,
        sort=True,
    )

    for group_values, group in grouped:
        (
            satellite_name,
            mission_code,
            norad_id,
            tle_name,
            tle_epoch_utc,
        ) = group_values

        elapsed = pd.to_numeric(
            group["elapsed_hours"],
            errors="coerce",
        )

        total_error = pd.to_numeric(
            group["total_error_km"],
            errors="coerce",
        )

        valid = pd.DataFrame(
            {
                "elapsed": elapsed,
                "error": total_error,
            }
        ).dropna()

        if len(valid) < 3:
            continue

        # Spearman evaluates monotonic association.
        correlation = spearmanr(
            valid["elapsed"],
            valid["error"],
        )

        rho = float(
            correlation.statistic
        )

        p_value = float(
            correlation.pvalue
        )

        results.append(
            TLECorrelationResult(
                satellite_name=str(
                    satellite_name
                ),
                mission_code=str(
                    mission_code
                ),
                norad_id=int(
                    norad_id
                ),
                tle_name=str(
                    tle_name
                ),
                tle_epoch_utc=str(
                    tle_epoch_utc
                ),
                observation_count=int(
                    len(valid)
                ),
                spearman_rho=rho,
                spearman_p_value=p_value,
            )
        )

    if not results:
        raise RuntimeError(
            "No valid TLE-level correlations could be calculated."
        )

    return pd.DataFrame(
        [
            asdict(result)
            for result in results
        ]
    )


# ---------------------------------------------------------------------
# TLE-level hypothesis test
# ---------------------------------------------------------------------

def summarize_correlations(
    correlation_table: pd.DataFrame,
    scope: str,
    alpha: float = 0.05,
) -> CorrelationSummary:
    """
    Summarize the per-TLE Spearman coefficients.

    The Wilcoxon signed-rank test evaluates whether the distribution
    of TLE-level rho values differs from zero.

    This makes the historical TLE, rather than each one-minute
    observation, the inferential unit.
    """

    rho_values = pd.to_numeric(
        correlation_table[
            "spearman_rho"
        ],
        errors="coerce",
    ).dropna()

    if rho_values.empty:
        raise ValueError(
            "No valid Spearman coefficients were available."
        )

    positive = int(
        (rho_values > 0).sum()
    )

    negative = int(
        (rho_values < 0).sum()
    )

    zero = int(
        (rho_values == 0).sum()
    )

    statistic: float | None
    p_value: float | None
    reject_null: bool | None

    nonzero_values = rho_values[
        rho_values != 0
    ]

    if len(nonzero_values) >= 2:
        test = wilcoxon(
            nonzero_values,
            alternative="two-sided",
            zero_method="wilcox",
        )

        statistic = float(
            test.statistic
        )

        p_value = float(
            test.pvalue
        )

        reject_null = (
            p_value < alpha
        )

    else:
        statistic = None
        p_value = None
        reject_null = None

    return CorrelationSummary(
        scope=scope,
        tle_count=int(
            len(rho_values)
        ),
        mean_rho=float(
            rho_values.mean()
        ),
        median_rho=float(
            rho_values.median()
        ),
        standard_deviation_rho=float(
            rho_values.std(
                ddof=1
            )
            if len(rho_values) > 1
            else 0.0
        ),
        minimum_rho=float(
            rho_values.min()
        ),
        maximum_rho=float(
            rho_values.max()
        ),
        positive_correlations=positive,
        negative_correlations=negative,
        zero_correlations=zero,
        wilcoxon_statistic=statistic,
        wilcoxon_p_value=p_value,
        significance_level=alpha,
        reject_null_hypothesis=(
            reject_null
        ),
    )


# ---------------------------------------------------------------------
# Per-satellite summaries
# ---------------------------------------------------------------------

def save_satellite_summaries(
    correlation_table: pd.DataFrame,
    output_folder: Path,
    alpha: float,
) -> list[dict]:
    """
    Produce separate TLE-level correlation summaries for each satellite.
    """

    summaries: list[dict] = []

    for satellite_name, group in (
        correlation_table.groupby(
            "satellite_name"
        )
    ):
        summary = summarize_correlations(
            correlation_table=group,
            scope=str(
                satellite_name
            ),
            alpha=alpha,
        )

        summaries.append(
            asdict(
                summary
            )
        )

    pd.DataFrame(
        summaries
    ).to_csv(
        output_folder
        / "time_error_summary_by_satellite.csv",
        index=False,
    )

    return summaries


# ---------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------

def save_rho_boxplot(
    correlation_table: pd.DataFrame,
    output_folder: Path,
) -> None:
    """
    Plot the distribution of TLE-level Spearman rho values.
    """

    satellites = list(
        correlation_table[
            "satellite_name"
        ].dropna().unique()
    )

    data = []

    labels = []

    for satellite in satellites:
        values = pd.to_numeric(
            correlation_table.loc[
                correlation_table[
                    "satellite_name"
                ]
                == satellite,
                "spearman_rho",
            ],
            errors="coerce",
        ).dropna()

        if values.empty:
            continue

        data.append(
            values.to_numpy()
        )

        labels.append(
            str(satellite)
        )

    if not data:
        return

    plt.figure(
        figsize=(8, 6)
    )

    plt.boxplot(
        data,
        tick_labels=labels,
    )

    plt.axhline(
        0.0,
        linewidth=1,
    )

    plt.title(
        "Propagation Time–Position Error Association"
    )

    plt.xlabel(
        "Satellite"
    )

    plt.ylabel(
        "TLE-level Spearman correlation (ρ)"
    )

    plt.tight_layout()

    plt.savefig(
        output_folder
        / "spearman_rho_by_satellite.png",
        dpi=300,
    )

    plt.close()


def save_median_error_curve(
    dataframe: pd.DataFrame,
    output_folder: Path,
) -> None:
    """
    Plot median total position error through the 24-hour propagation
    interval.

    Observations are grouped into 15-minute bins for visualization.
    This graph is descriptive only and is not used as the inferential
    hypothesis test.
    """

    working = dataframe.copy()

    working["elapsed_hours"] = (
        pd.to_numeric(
            working[
                "elapsed_hours"
            ],
            errors="coerce",
        )
    )

    working["total_error_km"] = (
        pd.to_numeric(
            working[
                "total_error_km"
            ],
            errors="coerce",
        )
    )

    working = working.dropna(
        subset=[
            "elapsed_hours",
            "total_error_km",
            "satellite_name",
        ]
    )

    if working.empty:
        return

    # 15-minute bins = 0.25 hour.
    working["time_bin"] = (
        np.floor(
            working["elapsed_hours"]
            / 0.25
        )
        * 0.25
    )

    plt.figure(
        figsize=(10, 6)
    )

    for satellite_name, group in (
        working.groupby(
            "satellite_name"
        )
    ):
        summary = (
            group.groupby(
                "time_bin"
            )[
                "total_error_km"
            ]
            .median()
            .reset_index()
        )

        plt.plot(
            summary["time_bin"],
            summary[
                "total_error_km"
            ],
            label=str(
                satellite_name
            ),
        )

    plt.title(
        "Median TLE–SGP4 Position Error "
        "Across Propagation Time"
    )

    plt.xlabel(
        "Elapsed propagation time (hours)"
    )

    plt.ylabel(
        "Median total position error (km)"
    )

    plt.legend()

    plt.tight_layout()

    plt.savefig(
        output_folder
        / "median_error_vs_time.png",
        dpi=300,
    )

    plt.close()


# ---------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------

def run_time_error_statistics(
    scope: str,
    alpha: float = 0.05,
) -> Path:
    """
    Run the TLE-level propagation-time/error association analysis.
    """

    ensure_app_directories()

    dataframe = load_timeseries(
        scope
    )

    output_folder = get_output_folder(
        scope
    )

    output_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    correlation_table = (
        calculate_tle_correlations(
            dataframe
        )
    )

    correlation_table.to_csv(
        output_folder
        / "tle_spearman_correlations.csv",
        index=False,
    )

    overall_summary = (
        summarize_correlations(
            correlation_table=(
                correlation_table
            ),
            scope=scope,
            alpha=alpha,
        )
    )

    satellite_summaries = (
        save_satellite_summaries(
            correlation_table=(
                correlation_table
            ),
            output_folder=(
                output_folder
            ),
            alpha=alpha,
        )
    )

    save_rho_boxplot(
        correlation_table,
        output_folder,
    )

    save_median_error_curve(
        dataframe,
        output_folder,
    )

    complete_summary = {
        "analysis": (
            "TLE-level Spearman association "
            "between elapsed propagation time "
            "and total position error"
        ),
        "inferential_unit": (
            "historical TLE"
        ),
        "significance_level": (
            alpha
        ),
        "overall": asdict(
            overall_summary
        ),
        "by_satellite": (
            satellite_summaries
        ),
    }

    with (
        output_folder
        / "time_error_statistics.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as json_file:
        json.dump(
            complete_summary,
            json_file,
            indent=2,
        )

    print("=" * 70)
    print(
        "ORBIT-TRUST Propagation Time–Error Statistics"
    )
    print("=" * 70)

    print(
        f"Analysis scope: {scope}"
    )

    print(
        f"TLEs analyzed: "
        f"{overall_summary.tle_count}"
    )

    print()

    print(
        "TLE-level Spearman coefficients:"
    )

    print(
        f"  Mean rho:   "
        f"{overall_summary.mean_rho:.4f}"
    )

    print(
        f"  Median rho: "
        f"{overall_summary.median_rho:.4f}"
    )

    print(
        f"  SD:         "
        f"{overall_summary.standard_deviation_rho:.4f}"
    )

    print(
        f"  Minimum:    "
        f"{overall_summary.minimum_rho:.4f}"
    )

    print(
        f"  Maximum:    "
        f"{overall_summary.maximum_rho:.4f}"
    )

    print()

    print(
        f"Positive TLE correlations: "
        f"{overall_summary.positive_correlations}"
    )

    print(
        f"Negative TLE correlations: "
        f"{overall_summary.negative_correlations}"
    )

    if (
        overall_summary.wilcoxon_p_value
        is not None
    ):
        print()

        print(
            "TLE-level Wilcoxon signed-rank test:"
        )

        print(
            f"  Statistic: "
            f"{overall_summary.wilcoxon_statistic:.4f}"
        )

        print(
            f"  p-value:   "
            f"{overall_summary.wilcoxon_p_value:.6g}"
        )

        if (
            overall_summary.reject_null_hypothesis
        ):
            print(
                "  Decision: Reject H0 at alpha = "
                f"{alpha:.2f}"
            )
        else:
            print(
                "  Decision: Fail to reject H0 at alpha = "
                f"{alpha:.2f}"
            )

    print()

    print(
        f"Statistics saved to:\n"
        f"{output_folder}"
    )

    print("=" * 70)

    return output_folder


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze the relationship between "
            "propagation time and total position error "
            "using TLE-level Spearman correlations."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=[
            "sentinel1a",
            "sentinel1b",
            "combined",
        ],
        default="combined",
        help=(
            "Analysis scope. "
            "Default: combined"
        ),
    )

    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help=(
            "Significance level. "
            "Default: 0.05"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if not (
        0.0
        < args.alpha
        < 1.0
    ):
        raise ValueError(
            "Alpha must be between 0 and 1."
        )

    run_time_error_statistics(
        scope=args.satellite,
        alpha=args.alpha,
    )


if __name__ == "__main__":
    main()
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.core.paths import RESULTS_DIR, STATISTICS_DIR


SUPPORTED_SCOPES = (
    "sentinel1a",
    "sentinel1b",
    "combined",
)

SCOPE_LABELS = {
    "sentinel1a": "Sentinel-1A",
    "sentinel1b": "Sentinel-1B",
    "combined": "Sentinel-1A and Sentinel-1B",
}

ERROR_METRIC_LABELS = [
    ("initial_error_km", "Initial position error"),
    ("final_error_km", "Final position error"),
    ("mean_error_km", "Mean position error"),
    ("maximum_error_km", "Maximum position error"),
    ("rms_error_km", "Total RMS position error"),
    ("radial_rms_km", "Radial RMS error"),
    ("along_track_rms_km", "Along-track RMS error"),
    ("cross_track_rms_km", "Cross-track RMS error"),
]


# ---------------------------------------------------------------------
# Paths and input loading
# ---------------------------------------------------------------------

def get_statistics_folder(scope: str) -> Path:
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(
            f"Unsupported report scope: {scope}"
        )

    return STATISTICS_DIR / scope


def get_summary_file(scope: str) -> Path:
    return (
        get_statistics_folder(scope)
        / "statistics_summary.json"
    )


def get_time_error_summary_file(scope: str) -> Path:
    return (
        get_statistics_folder(scope)
        / "time_error"
        / "time_error_statistics.json"
    )


def get_output_file(scope: str) -> Path:
    if scope == "combined":
        return RESULTS_DIR / "ORBIT_TRUST_Research_Report.pdf"

    return (
        RESULTS_DIR
        / f"ORBIT_TRUST_Research_Report_{scope}.pdf"
    )


def load_json_file(
    path: Path,
    description: str,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"{description} was not found:\n{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"{description} does not contain a JSON object:\n{path}"
        )

    return data


def load_statistics_summary(
    scope: str,
) -> dict[str, Any]:
    return load_json_file(
        get_summary_file(scope),
        "Statistics summary",
    )


def load_time_error_summary(
    scope: str,
) -> dict[str, Any] | None:
    path = get_time_error_summary_file(
        scope
    )

    if not path.exists():
        return None

    return load_json_file(
        path,
        "Propagation time/error summary",
    )


# ---------------------------------------------------------------------
# Experiment metadata
# ---------------------------------------------------------------------

def get_batch_files(
    scope: str,
) -> list[Path]:
    if scope == "sentinel1a":
        return [
            RESULTS_DIR
            / "sentinel1a_batch_reliability.csv"
        ]

    if scope == "sentinel1b":
        return [
            RESULTS_DIR
            / "sentinel1b_batch_reliability.csv"
        ]

    return [
        RESULTS_DIR
        / "sentinel1a_batch_reliability.csv",
        RESULTS_DIR
        / "sentinel1b_batch_reliability.csv",
    ]


def load_experiment_metadata(
    scope: str,
) -> dict[str, Any]:
    """
    Read the batch CSV files to obtain the actual date span and
    satellite/sample counts represented by the report.

    The statistics JSON remains the source for computed statistics.
    """

    epochs: list[datetime] = []
    counts: dict[str, int] = {}
    matched_record_total = 0

    for csv_path in get_batch_files(
        scope
    ):
        if not csv_path.exists():
            continue

        with csv_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as csv_file:
            reader = csv.DictReader(
                csv_file
            )

            for row in reader:
                satellite_name = (
                    row.get(
                        "satellite_name",
                        "",
                    )
                    or "Unknown"
                )

                counts[satellite_name] = (
                    counts.get(
                        satellite_name,
                        0,
                    )
                    + 1
                )

                epoch_text = (
                    row.get(
                        "tle_epoch_utc",
                        "",
                    )
                    or ""
                )

                if epoch_text:
                    try:
                        epochs.append(
                            datetime.fromisoformat(
                                epoch_text.replace(
                                    "Z",
                                    "+00:00",
                                )
                            )
                        )
                    except ValueError:
                        pass

                matched_text = (
                    row.get(
                        "matched_records",
                        "",
                    )
                    or ""
                )

                try:
                    matched_record_total += int(
                        float(
                            matched_text
                        )
                    )
                except ValueError:
                    pass

    start_date = None
    end_date = None

    if epochs:
        start_date = min(
            epochs
        ).date().isoformat()

        end_date = max(
            epochs
        ).date().isoformat()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "satellite_counts": counts,
        "batch_tle_count": sum(
            counts.values()
        ),
        "matched_record_total": (
            matched_record_total
        ),
    }


# ---------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------

def format_number(
    value: Any,
    decimals: int = 3,
    suffix: str = "",
) -> str:
    if value is None:
        return "N/A"

    try:
        return (
            f"{float(value):.{decimals}f}"
            f"{suffix}"
        )
    except (
        TypeError,
        ValueError,
    ):
        return str(
            value
        )


def format_p_value(
    value: Any,
) -> str:
    if value is None:
        return "N/A"

    try:
        number = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return str(
            value
        )

    if number < 0.0001:
        return f"{number:.3e}"

    return f"{number:.6f}"


def decision_text(
    rejected: Any,
) -> str:
    if rejected is True:
        return "Reject H0"

    if rejected is False:
        return "Fail to reject H0"

    return "Not determined"


def find_horizon(
    summary: dict[str, Any],
    threshold_km: float,
) -> dict[str, Any] | None:
    for item in summary.get(
        "reliability_horizons",
        [],
    ):
        try:
            if (
                float(
                    item.get(
                        "threshold_km"
                    )
                )
                == threshold_km
            ):
                return item
        except (
            TypeError,
            ValueError,
        ):
            continue

    return None


# ---------------------------------------------------------------------
# Styles and page layout
# ---------------------------------------------------------------------

def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "OrbitTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            textColor=colors.HexColor(
                "#123B5D"
            ),
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "OrbitSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            alignment=TA_CENTER,
            textColor=colors.HexColor(
                "#374151"
            ),
            spaceAfter=7,
        ),
        "heading": ParagraphStyle(
            "OrbitHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=colors.HexColor(
                "#123B5D"
            ),
            spaceBefore=10,
            spaceAfter=8,
        ),
        "subheading": ParagraphStyle(
            "OrbitSubheading",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor(
                "#123B5D"
            ),
            spaceBefore=7,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "OrbitBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            alignment=TA_LEFT,
            spaceAfter=7,
        ),
        "caption": ParagraphStyle(
            "OrbitCaption",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.HexColor(
                "#4B5563"
            ),
            spaceBefore=4,
            spaceAfter=10,
        ),
        "small": ParagraphStyle(
            "OrbitSmall",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor(
                "#4B5563"
            ),
        ),
        "warning": ParagraphStyle(
            "OrbitWarning",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor(
                "#7C2D12"
            ),
            backColor=colors.HexColor(
                "#FFF7ED"
            ),
            borderColor=colors.HexColor(
                "#FED7AA"
            ),
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=5,
            spaceAfter=8,
        ),
    }


def add_page_number(
    canvas,
    document,
) -> None:
    canvas.saveState()

    page_number = (
        canvas.getPageNumber()
    )

    width, _ = A4

    canvas.setFont(
        "Helvetica",
        8,
    )

    canvas.setFillColor(
        colors.HexColor(
            "#6B7280"
        )
    )

    canvas.drawString(
        1.6 * cm,
        1.0 * cm,
        "ORBIT-TRUST Research Report",
    )

    canvas.drawRightString(
        width - 1.6 * cm,
        1.0 * cm,
        f"Page {page_number}",
    )

    canvas.restoreState()


def style_table(
    table: Table,
    header: bool = True,
    centered: bool = False,
) -> Table:
    commands = [
        (
            "GRID",
            (0, 0),
            (-1, -1),
            0.4,
            colors.HexColor(
                "#CBD5E1"
            ),
        ),
        (
            "VALIGN",
            (0, 0),
            (-1, -1),
            "TOP",
        ),
        (
            "LEFTPADDING",
            (0, 0),
            (-1, -1),
            6,
        ),
        (
            "RIGHTPADDING",
            (0, 0),
            (-1, -1),
            6,
        ),
        (
            "TOPPADDING",
            (0, 0),
            (-1, -1),
            5,
        ),
        (
            "BOTTOMPADDING",
            (0, 0),
            (-1, -1),
            5,
        ),
    ]

    if header:
        commands.extend(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(
                        "#123B5D"
                    ),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [
                        colors.white,
                        colors.HexColor(
                            "#F4F7FA"
                        ),
                    ],
                ),
            ]
        )

    if centered:
        commands.append(
            (
                "ALIGN",
                (1, 1),
                (-1, -1),
                "CENTER",
            )
        )

    table.setStyle(
        TableStyle(
            commands
        )
    )

    return table


# ---------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------

def build_configuration_table(
    summary: dict[str, Any],
    metadata: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    satellite_counts = (
        summary.get(
            "satellite_counts",
            {},
        )
        or metadata.get(
            "satellite_counts",
            {},
        )
    )

    count_text = ", ".join(
        f"{name}: {count}"
        for name, count
        in satellite_counts.items()
    )

    if not count_text:
        count_text = "N/A"

    date_text = "N/A"

    if (
        metadata.get(
            "start_date"
        )
        and metadata.get(
            "end_date"
        )
    ):
        date_text = (
            f"{metadata['start_date']} "
            f"through "
            f"{metadata['end_date']}"
        )

    rows = [
        [
            Paragraph(
                "<b>Configuration</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Value</b>",
                styles["body"],
            ),
        ],
        [
            "Analysis scope",
            SCOPE_LABELS.get(
                summary.get(
                    "analysis_scope",
                    ""
                ),
                str(
                    summary.get(
                        "analysis_scope",
                        "N/A",
                    )
                ),
            ),
        ],
        [
            "TLE epoch date span",
            date_text,
        ],
        [
            "Daily sampling rule",
            (
                "Earliest valid TLE epoch "
                "per UTC calendar day"
            ),
        ],
        [
            "Satellites / TLE cases",
            count_text,
        ],
        [
            "Propagation window",
            (
                "Up to 24 hours from each "
                "sampled TLE epoch"
            ),
        ],
        [
            "Matched sampling interval",
            (
                "Approximately 60 seconds"
            ),
        ],
        [
            "Reliability thresholds",
            "1 km, 2 km, 5 km, 10 km",
        ],
        [
            "Reference source",
            (
                "Sentinel precise orbit "
                "ephemerides (AUX_POEORB)"
            ),
        ],
    ]

    return style_table(
        Table(
            rows,
            colWidths=[
                5.0 * cm,
                12.0 * cm,
            ],
            repeatRows=1,
        )
    )


def build_error_metrics_table(
    summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    error_metrics = (
        summary.get(
            "error_metrics",
            {}
        )
        or {}
    )

    rows: list[
        list[Any]
    ] = [
        [
            Paragraph(
                "<b>Metric</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Mean</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Median</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>SD</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>95% CI of mean</b>",
                styles["body"],
            ),
        ]
    ]

    for key, label in (
        ERROR_METRIC_LABELS
    ):
        metric = error_metrics.get(
            key
        )

        if not isinstance(
            metric,
            dict,
        ):
            continue

        ci_text = (
            f"{format_number(metric.get('confidence_interval_95_low'))}"
            " to "
            f"{format_number(metric.get('confidence_interval_95_high'))} km"
        )

        rows.append(
            [
                label,
                format_number(
                    metric.get(
                        "mean"
                    ),
                    3,
                    " km",
                ),
                format_number(
                    metric.get(
                        "median"
                    ),
                    3,
                    " km",
                ),
                format_number(
                    metric.get(
                        "standard_deviation"
                    ),
                    3,
                    " km",
                ),
                ci_text,
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                "No error metrics found",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
            ]
        )

    return style_table(
        Table(
            rows,
            colWidths=[
                5.0 * cm,
                2.6 * cm,
                2.6 * cm,
                2.6 * cm,
                4.2 * cm,
            ],
            repeatRows=1,
        ),
        centered=True,
    )


def build_horizon_table(
    summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    rows: list[
        list[Any]
    ] = [
        [
            Paragraph(
                "<b>Threshold</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Exact</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Lower bound</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Crossing rate</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Mean exact horizon</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Median exact horizon</b>",
                styles["body"],
            ),
        ]
    ]

    for item in summary.get(
        "reliability_horizons",
        [],
    ):
        exact_stats = item.get(
            "exact_horizon_statistics"
        )

        rows.append(
            [
                (
                    f"{format_number(item.get('threshold_km'), 0)} km"
                ),
                str(
                    item.get(
                        "exact_crossings",
                        0,
                    )
                ),
                str(
                    item.get(
                        "lower_bounds",
                        0,
                    )
                ),
                format_number(
                    item.get(
                        "crossing_percentage"
                    ),
                    1,
                    "%",
                ),
                format_number(
                    exact_stats.get(
                        "mean"
                    )
                    if isinstance(
                        exact_stats,
                        dict,
                    )
                    else None,
                    3,
                    " h",
                ),
                format_number(
                    exact_stats.get(
                        "median"
                    )
                    if isinstance(
                        exact_stats,
                        dict,
                    )
                    else None,
                    3,
                    " h",
                ),
            ]
        )

    return style_table(
        Table(
            rows,
            colWidths=[
                2.3 * cm,
                2.1 * cm,
                2.6 * cm,
                2.8 * cm,
                3.6 * cm,
                3.6 * cm,
            ],
            repeatRows=1,
        ),
        centered=True,
    )


def build_satellite_comparison_table(
    summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table | None:
    comparison = summary.get(
        "satellite_rms_comparison"
    )

    if not isinstance(
        comparison,
        dict,
    ):
        return None

    rows = [
        [
            Paragraph(
                "<b>Field</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Result</b>",
                styles["body"],
            ),
        ],
        [
            "Test",
            comparison.get(
                "test",
                "Mann-Whitney U",
            ),
        ],
        [
            str(
                comparison.get(
                    "group_1",
                    "Group 1",
                )
            ),
            (
                f"n = {comparison.get('group_1_n', 'N/A')}; "
                f"median RMS = "
                f"{format_number(comparison.get('group_1_median'), 3, ' km')}"
            ),
        ],
        [
            str(
                comparison.get(
                    "group_2",
                    "Group 2",
                )
            ),
            (
                f"n = {comparison.get('group_2_n', 'N/A')}; "
                f"median RMS = "
                f"{format_number(comparison.get('group_2_median'), 3, ' km')}"
            ),
        ],
        [
            "U statistic",
            format_number(
                comparison.get(
                    "statistic"
                ),
                3,
            ),
        ],
        [
            "p-value",
            format_p_value(
                comparison.get(
                    "p_value"
                )
            ),
        ],
        [
            "Software significance flag",
            (
                "Significant"
                if comparison.get(
                    "significant"
                )
                is True
                else "Not significant"
            ),
        ],
    ]

    return style_table(
        Table(
            rows,
            colWidths=[
                6.0 * cm,
                11.0 * cm,
            ],
            repeatRows=1,
        )
    )


def build_component_tests_table(
    summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table | None:
    tests = summary.get(
        "component_error_tests"
    )

    if not isinstance(
        tests,
        list,
    ) or not tests:
        return None

    rows: list[
        list[Any]
    ] = [
        [
            Paragraph(
                "<b>Satellite</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Test</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Comparison</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>n</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>p-value</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Adjusted p</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Significant</b>",
                styles["small"],
            ),
        ]
    ]

    for item in tests:
        if not isinstance(
            item,
            dict,
        ):
            continue

        adjusted = item.get(
            "bonferroni_adjusted_p"
        )

        rows.append(
            [
                str(
                    item.get(
                        "satellite_name",
                        "All",
                    )
                ),
                str(
                    item.get(
                        "test",
                        "N/A",
                    )
                ),
                str(
                    item.get(
                        "comparison",
                        "Overall components",
                    )
                ),
                str(
                    item.get(
                        "n_tles",
                        "N/A",
                    )
                ),
                format_p_value(
                    item.get(
                        "p_value"
                    )
                ),
                (
                    format_p_value(
                        adjusted
                    )
                    if adjusted
                    is not None
                    else "N/A"
                ),
                (
                    "Yes"
                    if item.get(
                        "significant"
                    )
                    is True
                    else "No"
                ),
            ]
        )

    return style_table(
        Table(
            rows,
            colWidths=[
                2.4 * cm,
                2.8 * cm,
                4.0 * cm,
                1.2 * cm,
                2.1 * cm,
                2.1 * cm,
                1.6 * cm,
            ],
            repeatRows=1,
        ),
        centered=True,
    )


def build_time_error_table(
    time_summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table:
    overall = (
        time_summary.get(
            "overall",
            {}
        )
        or {}
    )

    rows = [
        [
            Paragraph(
                "<b>Metric</b>",
                styles["body"],
            ),
            Paragraph(
                "<b>Result</b>",
                styles["body"],
            ),
        ],
        [
            "Inferential unit",
            time_summary.get(
                "inferential_unit",
                "Historical TLE",
            ),
        ],
        [
            "TLE count",
            str(
                overall.get(
                    "tle_count",
                    "N/A",
                )
            ),
        ],
        [
            "Mean Spearman rho",
            format_number(
                overall.get(
                    "mean_rho"
                ),
                4,
            ),
        ],
        [
            "Median Spearman rho",
            format_number(
                overall.get(
                    "median_rho"
                ),
                4,
            ),
        ],
        [
            "Minimum / maximum rho",
            (
                f"{format_number(overall.get('minimum_rho'), 4)}"
                " / "
                f"{format_number(overall.get('maximum_rho'), 4)}"
            ),
        ],
        [
            "Positive / negative TLE correlations",
            (
                f"{overall.get('positive_correlations', 'N/A')}"
                " / "
                f"{overall.get('negative_correlations', 'N/A')}"
            ),
        ],
        [
            "Wilcoxon statistic",
            format_number(
                overall.get(
                    "wilcoxon_statistic"
                ),
                4,
            ),
        ],
        [
            "Wilcoxon p-value",
            format_p_value(
                overall.get(
                    "wilcoxon_p_value"
                )
            ),
        ],
        [
            "H0 decision",
            decision_text(
                overall.get(
                    "reject_null_hypothesis"
                )
            ),
        ],
    ]

    return style_table(
        Table(
            rows,
            colWidths=[
                7.0 * cm,
                10.0 * cm,
            ],
            repeatRows=1,
        )
    )


def build_time_error_by_satellite_table(
    time_summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Table | None:
    items = time_summary.get(
        "by_satellite"
    )

    if not isinstance(
        items,
        list,
    ) or not items:
        return None

    rows: list[
        list[Any]
    ] = [
        [
            Paragraph(
                "<b>Satellite</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>n</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Mean rho</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Median rho</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>p-value</b>",
                styles["small"],
            ),
            Paragraph(
                "<b>Decision</b>",
                styles["small"],
            ),
        ]
    ]

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        rows.append(
            [
                str(
                    item.get(
                        "scope",
                        "N/A",
                    )
                ),
                str(
                    item.get(
                        "tle_count",
                        "N/A",
                    )
                ),
                format_number(
                    item.get(
                        "mean_rho"
                    ),
                    4,
                ),
                format_number(
                    item.get(
                        "median_rho"
                    ),
                    4,
                ),
                format_p_value(
                    item.get(
                        "wilcoxon_p_value"
                    )
                ),
                decision_text(
                    item.get(
                        "reject_null_hypothesis"
                    )
                ),
            ]
        )

    return style_table(
        Table(
            rows,
            colWidths=[
                3.7 * cm,
                1.4 * cm,
                2.8 * cm,
                2.8 * cm,
                2.8 * cm,
                3.5 * cm,
            ],
            repeatRows=1,
        ),
        centered=True,
    )


# ---------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------

def get_figure_files(
    scope: str,
) -> list[
    tuple[str, Path]
]:
    statistics_folder = (
        get_statistics_folder(
            scope
        )
    )

    time_folder = (
        statistics_folder
        / "time_error"
    )

    return [
        (
            "Distribution of total RMS position error",
            statistics_folder
            / "rms_error_histogram.png",
        ),
        (
            "RMS prediction error by TLE epoch",
            statistics_folder
            / "rms_error_by_epoch.png",
        ),
        (
            "Distribution of position-error metrics",
            statistics_folder
            / "error_metrics_boxplot.png",
        ),
        (
            "Radial, along-track, and cross-track RMS errors",
            statistics_folder
            / "component_rms_boxplot.png",
        ),
        (
            "Reliability-horizon distributions",
            statistics_folder
            / "reliability_horizon_boxplot.png",
        ),
        (
            "RMS error comparison by satellite",
            statistics_folder
            / "rms_error_by_satellite.png",
        ),
        (
            "Threshold-crossing comparison by satellite",
            statistics_folder
            / "threshold_crossing_by_satellite.png",
        ),
        (
            "TLE-level Spearman rho distribution",
            time_folder
            / "spearman_rho_by_satellite.png",
        ),
        (
            "Median position error across propagation time",
            time_folder
            / "median_error_vs_time.png",
        ),
    ]


def add_figures(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    scope: str,
) -> int:
    added = 0

    for caption, image_path in (
        get_figure_files(
            scope
        )
    ):
        if not image_path.exists():
            continue

        image = Image(
            str(
                image_path
            )
        )

        max_width = 17.0 * cm
        max_height = 10.8 * cm

        scale = min(
            max_width
            / image.imageWidth,
            max_height
            / image.imageHeight,
            1.0,
        )

        image.drawWidth = (
            image.imageWidth
            * scale
        )

        image.drawHeight = (
            image.imageHeight
            * scale
        )

        figure_number = (
            added + 1
        )

        story.append(
            KeepTogether(
                [
                    image,
                    Paragraph(
                        (
                            f"Figure {figure_number}. "
                            f"{caption}."
                        ),
                        styles[
                            "caption"
                        ],
                    ),
                ]
            )
        )

        added += 1

    if added == 0:
        story.append(
            Paragraph(
                (
                    "No generated figures were "
                    "found for this analysis scope."
                ),
                styles[
                    "body"
                ],
            )
        )

    return added


# ---------------------------------------------------------------------
# Narrative helpers
# ---------------------------------------------------------------------

def build_sample_size_note(
    summary: dict[str, Any],
    styles: dict[str, ParagraphStyle],
) -> Paragraph | None:
    try:
        tle_count = int(
            summary.get(
                "tle_count",
                0,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        tle_count = 0

    if tle_count >= 10:
        return None

    return Paragraph(
        (
            "<b>Small-sample diagnostic note:</b> "
            f"This report currently contains only {tle_count} "
            "TLE case(s). Inferential p-values and confidence "
            "intervals from such a small software-validation run "
            "must not be treated as final research conclusions. "
            "They are included to verify that the automated "
            "analysis and reporting pipeline operates correctly."
        ),
        styles[
            "warning"
        ],
    )


# ---------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------

def generate_report(
    scope: str = "combined",
) -> Path:
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(
            f"Unsupported report scope: {scope}"
        )

    summary = (
        load_statistics_summary(
            scope
        )
    )

    time_summary = (
        load_time_error_summary(
            scope
        )
    )

    metadata = (
        load_experiment_metadata(
            scope
        )
    )

    styles = build_styles()

    output_file = (
        get_output_file(
            scope
        )
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    document = SimpleDocTemplate(
        str(
            output_file
        ),
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.7 * cm,
        title=(
            "ORBIT-TRUST Research Report"
        ),
        author=(
            "Bayugan National "
            "Comprehensive High School"
        ),
        subject=(
            "TLE/SGP4 orbit-prediction "
            "reliability analysis"
        ),
    )

    story: list[Any] = []

    generated_at = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    # -------------------------------------------------------------
    # Cover page
    # -------------------------------------------------------------

    story.extend(
        [
            Spacer(
                1,
                1.8 * cm,
            ),
            Paragraph(
                "ORBIT-TRUST",
                styles[
                    "title"
                ],
            ),
            Paragraph(
                (
                    "Automated Assessment of "
                    "TLE/SGP4 Orbit-Prediction Reliability"
                ),
                styles[
                    "subtitle"
                ],
            ),
            Spacer(
                1,
                0.4 * cm,
            ),
            Paragraph(
                (
                    "<b>Analysis scope:</b> "
                    f"{SCOPE_LABELS[scope]}"
                ),
                styles[
                    "subtitle"
                ],
            ),
            Paragraph(
                (
                    "<b>Institution:</b> "
                    "Bayugan National Comprehensive High School"
                ),
                styles[
                    "subtitle"
                ],
            ),
            Paragraph(
                (
                    "<b>Report generated:</b> "
                    f"{generated_at}"
                ),
                styles[
                    "subtitle"
                ],
            ),
            Spacer(
                1,
                1.4 * cm,
            ),
            Paragraph(
                (
                    "This automated report summarizes the ORBIT-TRUST "
                    "computational experiment. Historical Two-Line "
                    "Element sets were propagated with SGP4 and compared "
                    "against Sentinel precise orbit ephemerides. The "
                    "framework quantified total and RTN position errors, "
                    "threshold-based reliability horizons, satellite-level "
                    "differences, and the association between elapsed "
                    "propagation time and position error."
                ),
                styles[
                    "body"
                ],
            ),
        ]
    )

    sample_note = (
        build_sample_size_note(
            summary,
            styles,
        )
    )

    if sample_note is not None:
        story.append(
            sample_note
        )

    story.append(
        PageBreak()
    )

    # -------------------------------------------------------------
    # 1. Experiment configuration
    # -------------------------------------------------------------

    story.extend(
        [
            Paragraph(
                "1. Experiment Configuration",
                styles[
                    "heading"
                ],
            ),
            Paragraph(
                (
                    "ORBIT-TRUST used the same predefined workflow for "
                    "each selected satellite. Historical TLE records were "
                    "deduplicated, grouped by UTC date, and sampled using "
                    "the earliest valid TLE epoch available for each day. "
                    "Every sampled TLE was evaluated independently over a "
                    "maximum 24-hour propagation window."
                ),
                styles[
                    "body"
                ],
            ),
            build_configuration_table(
                summary,
                metadata,
                styles,
            ),
        ]
    )

    # -------------------------------------------------------------
    # 2. Position-error statistics
    # -------------------------------------------------------------

    story.extend(
        [
            Paragraph(
                "2. Position-Error Statistics",
                styles[
                    "heading"
                ],
            ),
            Paragraph(
                (
                    "Position disagreement between the SGP4 prediction "
                    "and the precise reference trajectory was summarized "
                    "using total position error and radial, along-track, "
                    "and cross-track components. The table below reports "
                    "batch-level statistics across the sampled TLE cases."
                ),
                styles[
                    "body"
                ],
            ),
            build_error_metrics_table(
                summary,
                styles,
            ),
        ]
    )

    # -------------------------------------------------------------
    # 3. Reliability horizons
    # -------------------------------------------------------------

    story.extend(
        [
            Paragraph(
                "3. Reliability-Horizon Results",
                styles[
                    "heading"
                ],
            ),
            Paragraph(
                (
                    "For each selected threshold, an exact crossing means "
                    "the total position error reached or exceeded the "
                    "threshold during the 24-hour observation window. A "
                    "lower-bound case means no crossing was observed "
                    "within that window; therefore the true reliability "
                    "horizon is longer than the recorded observation time."
                ),
                styles[
                    "body"
                ],
            ),
            build_horizon_table(
                summary,
                styles,
            ),
        ]
    )

    # -------------------------------------------------------------
    # 4. Satellite and component comparisons
    # -------------------------------------------------------------

    story.append(
        Paragraph(
            "4. Comparative Statistical Tests",
            styles[
                "heading"
            ],
        )
    )

    comparison_table = (
        build_satellite_comparison_table(
            summary,
            styles,
        )
    )

    if comparison_table is not None:
        story.extend(
            [
                Paragraph(
                    (
                        "For a combined analysis, ORBIT-TRUST compares "
                        "the distribution of per-TLE total RMS errors "
                        "between Sentinel-1A and Sentinel-1B using a "
                        "two-sided Mann-Whitney U test."
                    ),
                    styles[
                        "body"
                    ],
                ),
                comparison_table,
                Spacer(
                    1,
                    0.35 * cm,
                ),
            ]
        )
    else:
        story.append(
            Paragraph(
                (
                    "A between-satellite RMS comparison is not available "
                    "for this single-satellite analysis scope."
                ),
                styles[
                    "body"
                ],
            )
        )

    component_table = (
        build_component_tests_table(
            summary,
            styles,
        )
    )

    if component_table is not None:
        story.extend(
            [
                Paragraph(
                    "RTN component comparison",
                    styles[
                        "subheading"
                    ],
                ),
                Paragraph(
                    (
                        "The component-analysis module evaluates whether "
                        "radial, along-track, and cross-track RMS errors "
                        "differ systematically across TLE cases. When "
                        "pairwise follow-up tests are produced, their "
                        "Bonferroni-adjusted p-values are reported."
                    ),
                    styles[
                        "body"
                    ],
                ),
                component_table,
            ]
        )

    # -------------------------------------------------------------
    # 5. Propagation time vs error
    # -------------------------------------------------------------

    story.append(
        Paragraph(
            (
                "5. Propagation Time and "
                "Total Position Error"
            ),
            styles[
                "heading"
            ],
        )
    )

    if time_summary is None:
        story.append(
            Paragraph(
                (
                    "Propagation time/error statistics were not found. "
                    "Run the time-error statistics module before "
                    "regenerating this report."
                ),
                styles[
                    "body"
                ],
            )
        )
    else:
        story.extend(
            [
                Paragraph(
                    (
                        "The detailed time-series dataset contains repeated "
                        "measurements within each TLE case. To avoid treating "
                        "those minute-level observations as independent "
                        "experiments, ORBIT-TRUST calculates one Spearman "
                        "correlation coefficient per historical TLE. The "
                        "TLE-level coefficients are then summarized and "
                        "tested against zero using a Wilcoxon signed-rank "
                        "test."
                    ),
                    styles[
                        "body"
                    ],
                ),
                build_time_error_table(
                    time_summary,
                    styles,
                ),
            ]
        )

        by_satellite_table = (
            build_time_error_by_satellite_table(
                time_summary,
                styles,
            )
        )

        if by_satellite_table is not None:
            story.extend(
                [
                    Spacer(
                        1,
                        0.3 * cm,
                    ),
                    Paragraph(
                        "Time-error results by satellite",
                        styles[
                            "subheading"
                        ],
                    ),
                    by_satellite_table,
                ]
            )

    # -------------------------------------------------------------
    # 6. Figures
    # -------------------------------------------------------------

    story.extend(
        [
            PageBreak(),
            Paragraph(
                "6. Generated Research Figures",
                styles[
                    "heading"
                ],
            ),
            Paragraph(
                (
                    "The following figures were generated automatically "
                    "from the same batch-level and time-series datasets "
                    "summarized in this report."
                ),
                styles[
                    "body"
                ],
            ),
        ]
    )

    add_figures(
        story,
        styles,
        scope,
    )

    # -------------------------------------------------------------
    # 7. Methodological notes
    # -------------------------------------------------------------

    story.extend(
        [
            PageBreak(),
            Paragraph(
                "7. Methodological Notes and Limitations",
                styles[
                    "heading"
                ],
            ),
            Paragraph(
                (
                    "SGP4 states are produced in the TEME reference frame "
                    "and are transformed into an Earth-fixed comparison "
                    "frame before being compared with the precise orbit "
                    "ephemerides. Error components are subsequently "
                    "expressed in radial, along-track, and cross-track form."
                ),
                styles[
                    "body"
                ],
            ),
            Paragraph(
                (
                    "Reliability-horizon observations that do not cross "
                    "the selected threshold within 24 hours are right-"
                    "censored lower bounds. They must not be interpreted "
                    "as exact threshold-crossing times."
                ),
                styles[
                    "body"
                ],
            ),
            Paragraph(
                (
                    "Confidence intervals and inferential tests describe "
                    "the TLE cases contained in the current dataset. Very "
                    "small diagnostic or pilot samples can produce unstable "
                    "confidence intervals and low statistical power; such "
                    "outputs should be treated as software-validation "
                    "results rather than final scientific conclusions."
                ),
                styles[
                    "body"
                ],
            ),
            Paragraph(
                (
                    "The framework validates TLE/SGP4 predictions only "
                    "where sufficiently accurate reference ephemerides are "
                    "available. Generalization beyond the analyzed "
                    "Sentinel missions requires equivalent reference-orbit "
                    "quality and the same controlled processing rules."
                ),
                styles[
                    "body"
                ],
            ),
            Spacer(
                1,
                0.45 * cm,
            ),
            Paragraph(
                (
                    "Generated automatically by ORBIT-TRUST. Statistical "
                    "outputs should be reviewed by the research team before "
                    "being incorporated into the final manuscript."
                ),
                styles[
                    "small"
                ],
            ),
        ]
    )

    document.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )

    return output_file


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a multi-satellite "
            "ORBIT-TRUST PDF research report."
        )
    )

    parser.add_argument(
        "--satellite",
        choices=list(
            SUPPORTED_SCOPES
        ),
        default="combined",
        help=(
            "Report scope. "
            "Default: combined"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    output_path = generate_report(
        scope=args.satellite
    )

    print(
        "Research report generated successfully."
    )

    print(
        f"Scope: {args.satellite}"
    )

    print(
        f"Output:\n{output_path}"
    )


if __name__ == "__main__":
    main()
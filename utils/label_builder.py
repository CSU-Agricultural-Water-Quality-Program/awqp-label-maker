from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product

import pandas as pd


LABEL_COLUMNS = [
    "Sample ID ",
    "Irr/Str",
    "Date",
    "Analysis",
    "Analyses Code",
    "Perserved",
    "Volume",
    "Label",
]

EVENT_COLUMNS = [
    "Sample ID ",
    "Irr/Str",
    "Date",
    "Analysis",
    "Analyses Code",
    "Perserved",
    "Volume",
    "Comment ",
]

ALS_COLUMNS = [
    "Sample ID ",
    "Irr/Str",
    "Date",
    "Analysis",
    "Analyses Code",
    "Perserved",
    "Volume",
    "Comments",
]


@dataclass
class SampleRow:
    sample_id: str
    irr_str: str
    collection_date: str
    analysis: str
    analyses_code: str
    preserved: str
    volume: int | str
    comment: str
    label: str
    exclude_from_als: bool


def empty_plan() -> dict:
    return {"groups": []}


def build_base_id(
    config: dict,
    *,
    location_key: str,
    treatment_key: str,
    event_number: str,
    event_type_key: str,
    method_key: str,
) -> str:
    parts = [config["locations"][location_key]["id"]]
    treatment_id = config["treatments"][treatment_key]["id"]
    event_type_id = config["event_types"][event_type_key]["id"]
    method_id = config["sample_methods"][method_key]["id"]

    if treatment_id:
        parts.append(treatment_id)
    parts.append(event_number)
    if event_type_id:
        parts.append(event_type_id)
    parts.append(method_id)
    return "-".join(parts)


def add_group_to_plan(
    plan: dict,
    *,
    config: dict,
    location_key: str,
    treatment_keys: list[str],
    event_type_key: str,
    method_keys: list[str],
    event_number: str,
    irrigation_or_storm: str,
    duplicate_key: str,
    analyte_keys: list[str],
    custom_comment: str,
) -> None:
    plan["groups"].append(
        {
            "location_key": location_key,
            "treatment_keys": treatment_keys,
            "event_type_key": event_type_key,
            "method_keys": method_keys,
            "event_number": event_number,
            "irrigation_or_storm": irrigation_or_storm,
            "duplicate_key": duplicate_key,
            "analyte_keys": analyte_keys,
            "custom_comment": custom_comment,
        }
    )


def remove_group_from_plan(plan: dict, index: int) -> None:
    plan["groups"].pop(index)


def make_label(sample_id: str, analyte: dict) -> str:
    suffix = analyte.get("label_suffix", "").strip()
    return sample_id if not suffix else f"{sample_id}\n{suffix}"


def build_sample_id(base_id: str, analyte_id: str, duplicate_id: str) -> str:
    sample_id = f"{base_id}-{analyte_id}"
    return sample_id if not duplicate_id else f"{sample_id}-{duplicate_id}"


def collect_rows(
    plan: dict,
    config: dict,
    *,
    collection_date: date,
    include_lab_blank: bool,
    blank_location_key: str,
) -> list[SampleRow]:
    rows: list[SampleRow] = []
    date_str = collection_date.strftime("%m/%d/%Y")

    for group in plan["groups"]:
        duplicate_id = config["duplicates"][group["duplicate_key"]]["id"]
        for treatment_key, method_key in product(
            group["treatment_keys"], group["method_keys"]
        ):
            base_id = build_base_id(
                config,
                location_key=group["location_key"],
                treatment_key=treatment_key,
                event_number=group["event_number"],
                event_type_key=group["event_type_key"],
                method_key=method_key,
            )
            for analyte_key in group["analyte_keys"]:
                analyte = config["analytes"][analyte_key]
                sample_id = build_sample_id(base_id, analyte["id"], duplicate_id)
                comment = group["custom_comment"] or analyte.get("comment", "")
                rows.append(
                    SampleRow(
                        sample_id=sample_id,
                        irr_str=group["irrigation_or_storm"],
                        collection_date=date_str,
                        analysis=analyte["analysis"],
                        analyses_code=analyte["analyses_code"],
                        preserved=analyte["preserved"],
                        volume=analyte["volume_ml"],
                        comment=comment,
                        label=make_label(sample_id, analyte),
                        exclude_from_als=analyte.get("exclude_from_als", False),
                    )
                )

    if include_lab_blank and plan["groups"]:
        event_number = plan["groups"][0]["event_number"]
        blank_prefix = f"BK-{config['locations'][blank_location_key]['id']}-{event_number}"
        irr_str = plan["groups"][0]["irrigation_or_storm"]
        for analyte in config["analytes"].values():
            if not analyte.get("include_in_blank"):
                continue
            sample_id = f"{blank_prefix}-{analyte['id']}"
            rows.append(
                SampleRow(
                    sample_id=sample_id,
                    irr_str=irr_str,
                    collection_date=date_str,
                    analysis=analyte["analysis"],
                    analyses_code=analyte["analyses_code"],
                    preserved=analyte["preserved"],
                    volume=analyte["volume_ml"],
                    comment=analyte.get("comment", ""),
                    label=make_label(sample_id, analyte),
                    exclude_from_als=analyte.get("exclude_from_als", False),
                )
            )

    return rows


def build_output_tables(
    plan: dict,
    config: dict,
    *,
    collection_date: date,
    include_lab_blank: bool,
    blank_location_key: str,
) -> dict[str, pd.DataFrame]:
    rows = collect_rows(
        plan,
        config,
        collection_date=collection_date,
        include_lab_blank=include_lab_blank,
        blank_location_key=blank_location_key,
    )

    label_rows = []
    event_rows = []
    als_rows = []

    for row in rows:
        label_rows.append(
            {
                "Sample ID ": row.sample_id,
                "Irr/Str": row.irr_str,
                "Date": row.collection_date,
                "Analysis": row.analysis,
                "Analyses Code": row.analyses_code,
                "Perserved": row.preserved,
                "Volume": row.volume,
                "Label": row.label,
            }
        )
        event_rows.append(
            {
                "Sample ID ": row.sample_id,
                "Irr/Str": row.irr_str,
                "Date": row.collection_date,
                "Analysis": row.analysis,
                "Analyses Code": row.analyses_code,
                "Perserved": row.preserved,
                "Volume": row.volume,
                "Comment ": row.comment,
            }
        )
        if not row.exclude_from_als:
            als_rows.append(
                {
                    "Sample ID ": row.sample_id,
                    "Irr/Str": row.irr_str,
                    "Date": row.collection_date,
                    "Analysis": row.analysis,
                    "Analyses Code": row.analyses_code,
                    "Perserved": row.preserved,
                    "Volume": row.volume,
                    "Comments": row.comment,
                }
            )

    return {
        "Labels": pd.DataFrame(label_rows, columns=LABEL_COLUMNS),
        "Event": pd.DataFrame(event_rows, columns=EVENT_COLUMNS),
        "For ALS Lab COC": pd.DataFrame(als_rows, columns=ALS_COLUMNS),
    }

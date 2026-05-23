from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

COUNTRY_TO_ENTSOE_AREA = {
    "BE": "BE",
    "FR": "FR",
    "DE": "DE_LU",
}


@dataclass(frozen=True)
class VariableSpec:
    name: str
    start_date: str
    unit: str
    fetcher: str
    psr_type: str | None = None


VARIABLES: dict[str, VariableSpec] = {
    "actual_load": VariableSpec("actual_load", "2020-12-01", "MW", "actual_load"),
    "forecast_load": VariableSpec("forecast_load", "2020-12-01", "MW", "forecast_load"),
    "actual_solar_generation": VariableSpec(
        "actual_solar_generation", "2022-01-01", "MW", "actual_generation", "B16"
    ),
    "forecast_solar_generation": VariableSpec(
        "forecast_solar_generation", "2022-01-01", "MW", "forecast_generation", "B16"
    ),
    "actual_onshore_wind_generation": VariableSpec(
        "actual_onshore_wind_generation", "2022-01-01", "MW", "actual_generation", "B19"
    ),
    "forecast_onshore_wind_generation": VariableSpec(
        "forecast_onshore_wind_generation", "2022-01-01", "MW", "forecast_generation", "B19"
    ),
    "actual_offshore_wind_generation": VariableSpec(
        "actual_offshore_wind_generation", "2022-01-01", "MW", "actual_generation", "B18"
    ),
    "forecast_offshore_wind_generation": VariableSpec(
        "forecast_offshore_wind_generation", "2022-01-01", "MW", "forecast_generation", "B18"
    ),
    "day_ahead_price": VariableSpec("day_ahead_price", "2020-12-01", "EUR/MWh", "day_ahead_price"),
    "imbalance_price": VariableSpec("imbalance_price", "2020-12-01", "EUR/MWh", "imbalance_price"),
}


@dataclass(frozen=True)
class Settings:
    api_key: str
    countries: tuple[str, ...]
    timezone: str
    data_dir: Path
    update_dir: Path
    update_manifest: Path
    run_history: Path
    status_file: Path
    fetch_mode: str
    storage_mode: str
    recent_days: int
    chunk_days: int
    sleep_seconds: float
    max_retries: int
    retry_sleep_seconds: float
    min_chunk_hours: int
    request_timeout_seconds: int
    progress_file: Path


def load_settings(require_api_key: bool = True) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("ENTSOE_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise RuntimeError("ENTSOE_API_KEY is not set. Copy .env.example to .env and add the token.")

    countries = tuple(
        country.strip().upper()
        for country in os.getenv("ENTSOE_COUNTRIES", "BE,FR,DE").split(",")
        if country.strip()
    )
    unknown = sorted(set(countries) - set(COUNTRY_TO_ENTSOE_AREA))
    if unknown:
        raise ValueError(f"Unknown country code(s): {unknown}. Supported: {sorted(COUNTRY_TO_ENTSOE_AREA)}")

    return Settings(
        api_key=api_key,
        countries=countries,
        timezone=os.getenv("ENTSOE_TIMEZONE", "Europe/Brussels"),
        data_dir=PROJECT_ROOT / os.getenv("ENTSOE_DATA_DIR", "data/raw"),
        update_dir=PROJECT_ROOT / os.getenv("ENTSOE_UPDATE_DIR", "data/updates"),
        update_manifest=PROJECT_ROOT / os.getenv("ENTSOE_UPDATE_MANIFEST", "data/update_manifest.csv"),
        run_history=PROJECT_ROOT / os.getenv("ENTSOE_RUN_HISTORY", "data/run_history.csv"),
        status_file=PROJECT_ROOT / os.getenv("ENTSOE_STATUS_FILE", "data/status.json"),
        fetch_mode=os.getenv("ENTSOE_FETCH_MODE", "recent").lower(),
        storage_mode=os.getenv("ENTSOE_STORAGE_MODE", "snapshot").lower(),
        recent_days=int(os.getenv("ENTSOE_RECENT_DAYS", "31")),
        chunk_days=int(os.getenv("ENTSOE_CHUNK_DAYS", "31")),
        sleep_seconds=float(os.getenv("ENTSOE_SLEEP_SECONDS", "1.0")),
        max_retries=int(os.getenv("ENTSOE_MAX_RETRIES", "3")),
        retry_sleep_seconds=float(os.getenv("ENTSOE_RETRY_SLEEP_SECONDS", "5.0")),
        min_chunk_hours=int(os.getenv("ENTSOE_MIN_CHUNK_HOURS", "24")),
        request_timeout_seconds=int(os.getenv("ENTSOE_REQUEST_TIMEOUT_SECONDS", "60")),
        progress_file=PROJECT_ROOT / os.getenv("ENTSOE_PROGRESS_FILE", "data/progress.json"),
    )

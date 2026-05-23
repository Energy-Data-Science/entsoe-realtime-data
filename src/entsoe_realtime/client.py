from __future__ import annotations

import time
import logging
from collections.abc import Callable
from collections.abc import Iterator

import pandas as pd
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError
from requests import ConnectionError, HTTPError, Timeout

from entsoe_realtime.config import COUNTRY_TO_ENTSOE_AREA, Settings, VariableSpec


STANDARD_COLUMNS = [
    "timestamp_utc",
    "timestamp_local",
    "value",
    "unit",
    "country",
    "entsoe_area",
    "variable",
    "source",
    "updated_at_utc",
]

logger = logging.getLogger(__name__)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class EntsoeFetcher:
    def __init__(self, settings: Settings, progress_callback: Callable[[dict], None] | None = None):
        self.settings = settings
        self.client = EntsoePandasClient(
            api_key=settings.api_key,
            retry_count=1,
            timeout=settings.request_timeout_seconds,
        )
        self.warnings: list[str] = []
        self.progress_callback = progress_callback

    def fetch_variable(self, country: str, spec: VariableSpec, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        country = country.upper()
        entsoe_area = COUNTRY_TO_ENTSOE_AREA[country]
        frames: list[pd.DataFrame] = []
        self.warnings = []

        for chunk_start, chunk_end in chunk_timerange(start, end, self.settings.chunk_days):
            self._emit_progress(country, spec, chunk_start, chunk_end, "fetching")
            frames.extend(self._fetch_range(country, entsoe_area, spec, chunk_start, chunk_end))
            self._emit_progress(country, spec, chunk_start, chunk_end, "chunk_complete")
            time.sleep(self.settings.sleep_seconds)

        if not frames:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        return pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["timestamp_utc", "country", "variable", "source"], keep="last"
        )

    def _emit_progress(
        self,
        country: str,
        spec: VariableSpec,
        start: pd.Timestamp,
        end: pd.Timestamp,
        status: str,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            {
                "country": country,
                "variable": spec.name,
                "chunk_start": start.isoformat(),
                "chunk_end": end.isoformat(),
                "status": status,
                "updated_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    def _fetch_range(
        self,
        country: str,
        entsoe_area: str,
        spec: VariableSpec,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> list[pd.DataFrame]:
        try:
            raw = self._fetch_chunk_with_retries(entsoe_area, spec, start, end)
        except NoMatchingDataError:
            logger.info("%s %s: no matching ENTSO-E data for %s to %s", country, spec.name, start, end)
            return []
        except Exception as exc:
            if not is_transient_error(exc):
                raise

            if not can_split_range(start, end, self.settings.min_chunk_hours):
                warning = f"{country} {spec.name}: skipped transiently failing window {start} to {end}"
                logger.error(warning)
                self.warnings.append(warning)
                return []

            midpoint = start + (end - start) / 2
            logger.warning(
                "%s %s: splitting transiently failing ENTSO-E window %s to %s",
                country,
                spec.name,
                start,
                end,
            )
            return [
                *self._fetch_range(country, entsoe_area, spec, start, midpoint),
                *self._fetch_range(country, entsoe_area, spec, midpoint, end),
            ]

        normalized = normalize_entsoe_response(raw, country, entsoe_area, spec, self.settings.timezone)
        return [] if normalized.empty else [normalized]

    def _fetch_chunk_with_retries(self, entsoe_area: str, spec: VariableSpec, start: pd.Timestamp, end: pd.Timestamp):
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                logger.info("%s: fetching %s to %s", spec.name, start, end)
                return self._fetch_chunk(entsoe_area, spec, start, end)
            except NoMatchingDataError:
                raise
            except Exception as exc:
                last_error = exc
                if not is_transient_error(exc) or attempt >= self.settings.max_retries:
                    break
                wait_seconds = self.settings.retry_sleep_seconds * (2**attempt)
                logger.warning(
                    "%s %s to %s failed with transient ENTSO-E error; retrying in %.1fs",
                    spec.name,
                    start,
                    end,
                    wait_seconds,
                )
                time.sleep(wait_seconds)

        if last_error is None:
            raise RuntimeError("ENTSO-E fetch failed without an exception.")
        raise last_error

    def _fetch_chunk(self, entsoe_area: str, spec: VariableSpec, start: pd.Timestamp, end: pd.Timestamp):
        if spec.fetcher == "actual_load":
            return self.client.query_load(entsoe_area, start=start, end=end)
        if spec.fetcher == "forecast_load":
            return self.client.query_load_forecast(entsoe_area, start=start, end=end)
        if spec.fetcher == "actual_generation":
            return self.client.query_generation(entsoe_area, start=start, end=end, psr_type=spec.psr_type)
        if spec.fetcher == "forecast_generation":
            return self.client.query_wind_and_solar_forecast(
                entsoe_area, start=start, end=end, psr_type=spec.psr_type
            )
        if spec.fetcher == "day_ahead_price":
            return self.client.query_day_ahead_prices(entsoe_area, start=start, end=end)
        if spec.fetcher == "imbalance_price":
            return self.client.query_imbalance_prices(entsoe_area, start=start, end=end)
        raise ValueError(f"Unsupported fetcher: {spec.fetcher}")


def make_time_window(spec: VariableSpec, settings: Settings) -> tuple[pd.Timestamp, pd.Timestamp]:
    now = pd.Timestamp.now(tz=settings.timezone).ceil("15min")
    full_start = pd.Timestamp(spec.start_date, tz=settings.timezone)

    if settings.fetch_mode == "recent":
        start = max(full_start, now - pd.Timedelta(days=settings.recent_days))
    elif settings.fetch_mode == "full":
        start = full_start
    else:
        raise ValueError("ENTSOE_FETCH_MODE must be either 'full' or 'recent'.")

    return start, now


def chunk_timerange(start: pd.Timestamp, end: pd.Timestamp, chunk_days: int) -> Iterator[tuple[pd.Timestamp, pd.Timestamp]]:
    cursor = start
    delta = pd.Timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, Timeout)):
        return True
    if isinstance(exc, HTTPError):
        response = getattr(exc, "response", None)
        return response is not None and response.status_code in TRANSIENT_STATUS_CODES
    return False


def can_split_range(start: pd.Timestamp, end: pd.Timestamp, min_chunk_hours: int) -> bool:
    return (end - start) > pd.Timedelta(hours=min_chunk_hours)


def normalize_entsoe_response(
    raw,
    country: str,
    entsoe_area: str,
    spec: VariableSpec,
    timezone: str,
) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    if isinstance(raw, pd.Series):
        frame = raw.rename("value").to_frame()
        frame["source"] = raw.name or "value"
        long = frame.reset_index()
    elif isinstance(raw, pd.DataFrame):
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [" | ".join(str(part) for part in col if str(part) != "") for col in frame.columns]
        frame = frame.reset_index()
        index_col = frame.columns[0]
        long = frame.melt(id_vars=[index_col], var_name="source", value_name="value")
    else:
        raise TypeError(f"Unsupported ENTSO-E response type: {type(raw)!r}")

    if long.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    timestamp_col = long.columns[0]
    timestamps = pd.to_datetime(long[timestamp_col])
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(timezone)

    result = pd.DataFrame(
        {
            "timestamp_utc": timestamps.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timestamp_local": timestamps.dt.tz_convert(timezone).dt.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "value": pd.to_numeric(long["value"], errors="coerce"),
            "unit": spec.unit,
            "country": country,
            "entsoe_area": entsoe_area,
            "variable": spec.name,
            "source": long.get("source", "value").astype(str),
            "updated_at_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    result = result.dropna(subset=["value"])
    return result[STANDARD_COLUMNS].sort_values(["timestamp_utc", "source"]).reset_index(drop=True)

"""Architecture B2 -- true minutes-to-event from the production calendar.

The Shadow Activation prototype fed the event-risk gate representative tier
midpoints (0 / 60 / 240 minutes) because production exposes an event *tier*
rather than a countdown. That was honest but imprecise, and a record showing
``0m`` could be misread as an exact measurement.

The production calendar already carries a real, timezone-aware release
timestamp on every event:

    {"title": "ISM Manufacturing PMI", "country": "USD",
     "date": "2026-09-01T10:00:00-04:00", "impact": "High", ...}

So this module computes the genuine minutes-to-event by reading those
timestamps. It is pure: the caller fetches the calendar through the existing
production fetcher and passes the list in. No production calendar logic is
touched, no extra request is made, and nothing is written.

When no relevant event is in range, or a timestamp cannot be parsed, the result
is explicitly ``unavailable`` -- never an invented time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

#: Impact labels production treats as high impact, matched case-insensitively.
HIGH_IMPACT_LABELS = frozenset({"high", "red", "high impact"})

#: The relevance window production itself uses: from 15 minutes after a release
#: to 12 hours before one. Reused so B2 and production agree on which event is
#: "the nearest relevant one" rather than drifting apart.
WINDOW_MINUTES_AFTER = -15.0
WINDOW_MINUTES_BEFORE = 720.0


@dataclass(frozen=True)
class EventTiming:
    """Timing of the nearest relevant high-impact event, or an honest absence."""

    minutes: float | None
    title: str
    currency: str
    source: str
    reason: str

    @property
    def is_available(self) -> bool:
        return self.minutes is not None and self.source == "calendar_timestamp"

    def as_record(self) -> dict[str, object]:
        return {
            "minutes_to_event": self.minutes,
            "event_title": self.title,
            "event_currency": self.currency,
            "event_timing_source": self.source,
            "event_timing_reason": self.reason,
        }


UNAVAILABLE_NO_CALENDAR = EventTiming(
    minutes=None,
    title="",
    currency="",
    source="unavailable",
    reason="No calendar data was available for this evaluation.",
)


def _parse_release(raw: Any) -> datetime | None:
    """Parse a production calendar timestamp, or return None. Never guesses."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def minutes_to_nearest_event(
    events: Iterable[Mapping[str, Any]] | None,
    relevant_currencies: Iterable[str],
    now: datetime,
) -> EventTiming:
    """True minutes to the nearest relevant high-impact event.

    Negative minutes mean the release has already happened, matching the
    convention the event-risk gate already uses.
    """
    if not events:
        return UNAVAILABLE_NO_CALENDAR

    reference = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    wanted = {str(c).strip().upper() for c in relevant_currencies if str(c).strip()}

    nearest: tuple[float, Mapping[str, Any]] | None = None
    unparseable = 0
    considered = 0

    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("impact", "")).strip().lower() not in HIGH_IMPACT_LABELS:
            continue
        currency = str(event.get("country", event.get("currency", ""))).strip().upper()
        if wanted and currency not in wanted:
            continue

        considered += 1
        release = _parse_release(event.get("date"))
        if release is None:
            unparseable += 1
            continue

        minutes = (release - reference).total_seconds() / 60.0
        if not (WINDOW_MINUTES_AFTER <= minutes <= WINDOW_MINUTES_BEFORE):
            continue
        if nearest is None or abs(minutes) < abs(nearest[0]):
            nearest = (minutes, event)

    if nearest is None:
        if considered and unparseable == considered:
            return EventTiming(
                minutes=None,
                title="",
                currency="",
                source="unavailable",
                reason=(
                    f"{unparseable} relevant high-impact event(s) had an "
                    "unparseable release timestamp; timing is unknown rather "
                    "than assumed clear."
                ),
            )
        return EventTiming(
            minutes=None,
            title="",
            currency="",
            source="calendar_timestamp",
            reason=(
                "No relevant high-impact event falls inside the evaluated "
                "proximity window."
            ),
        )

    minutes, event = nearest
    return EventTiming(
        minutes=round(minutes, 2),
        title=str(event.get("title", "")).strip(),
        currency=str(event.get("country", event.get("currency", ""))).strip().upper(),
        source="calendar_timestamp",
        reason="Computed from the production calendar's own release timestamp.",
    )


def is_top_tier(timing: EventTiming) -> bool:
    """Whether the nearest event is a high-impact release for this instrument.

    Every event this module selects is already high impact for a relevant
    currency, so a selected event is top tier by construction. Kept as an
    explicit function so the gate's contract stays readable at the call site.
    """
    return timing.minutes is not None and timing.source == "calendar_timestamp"

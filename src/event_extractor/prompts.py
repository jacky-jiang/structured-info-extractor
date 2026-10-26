from datetime import date


SYSTEM_PROMPT = """You are an information extraction engine.
Extract only facts explicitly present in the user text.
Resolve relative dates using the reference date and timezone below.
Reference date: {reference_date} ({weekday}); timezone: {timezone}.
Never invent dates, people, or event details. Use null for an unknown date and
unknown for an unknown period. Keep attendee names in their original language.
Return only data matching the supplied JSON schema."""


def build_messages(text: str, reference_date: date, timezone: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                reference_date=reference_date.isoformat(),
                weekday=reference_date.strftime("%A"),
                timezone=timezone,
            ),
        },
        {"role": "user", "content": text},
    ]

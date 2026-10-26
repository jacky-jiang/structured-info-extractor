import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .client import ModelClient
from .errors import ConfigurationError, ExtractorError
from .prompts import build_messages
from .usage import CostRates


def iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def positive_decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Expected a decimal number") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("Price cannot be negative")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured event data with an LLM.")
    parser.add_argument("text", nargs="?", help="Input text; omit to read interactively")
    parser.add_argument("--reference-date", type=iso_date, default=date.today())
    parser.add_argument("--timezone", default="local")
    parser.add_argument("--stream", action="store_true", help="Stream JSON deltas to stderr")
    parser.add_argument("--tools", action="store_true", help="Enable the calendar tool-calling demo")
    parser.add_argument("--show-messages", action="store_true", help="Print system/user messages to stderr")
    parser.add_argument("--input-price", type=positive_decimal, help="Input price per 1M tokens")
    parser.add_argument("--output-price", type=positive_decimal, help="Output price per 1M tokens")
    return parser


def run(args: argparse.Namespace) -> None:
    text = args.text or input("请输入待提取文本：").strip()
    if not text:
        raise ConfigurationError("Input text cannot be empty.")
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("MODEL")
    if not api_key or not model:
        raise ConfigurationError("Set API_KEY and MODEL; BASE_URL is optional. See .env.example.")

    if args.show_messages:
        print(json.dumps(build_messages(text, args.reference_date, args.timezone), ensure_ascii=False, indent=2), file=sys.stderr)

    on_delta = None
    if args.stream:
        def on_delta(delta: str) -> None:
            print(delta, end="", flush=True, file=sys.stderr)

    with ModelClient(api_key, base_url, model) as client:
        result = client.extract(
            text,
            reference_date=args.reference_date,
            timezone=args.timezone,
            on_delta=on_delta,
            use_tools=args.tools,
        )
    if args.stream:
        print(file=sys.stderr)

    print(json.dumps(result.data.model_dump(), ensure_ascii=False, indent=2))
    usage = result.usage
    if usage.reported:
        print(
            f"Tokens: input={usage.prompt_tokens}, output={usage.completion_tokens}, total={usage.total_tokens}",
            file=sys.stderr,
        )
    else:
        print("Tokens: provider did not return usage.", file=sys.stderr)

    input_price = args.input_price if args.input_price is not None else _env_decimal("INPUT_PRICE_PER_MILLION")
    output_price = args.output_price if args.output_price is not None else _env_decimal("OUTPUT_PRICE_PER_MILLION")
    if (input_price is None) != (output_price is None):
        raise ConfigurationError("Set both input and output prices to calculate cost.")
    if input_price is not None and output_price is not None:
        cost = CostRates(input_price, output_price).calculate(usage)
        if cost is not None:
            print(f"Cost: {cost:.8f} (billing currency configured by your prices)", file=sys.stderr)


def _env_decimal(name: str) -> Decimal | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} must be a decimal number.") from exc


def main() -> None:
    try:
        run(build_parser().parse_args())
    except ExtractorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()

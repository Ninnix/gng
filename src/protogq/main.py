from .ai_manager import AIGenerator


def main() -> None:
    ai_client = AIGenerator()
    response = ai_client.generate(
        "News of the day in Italy",
        model="qwen/qwen3.6-27b",
        with_web=True,
        show_thinking=False,
    )
    print(response)


if __name__ == "__main__":
    main()

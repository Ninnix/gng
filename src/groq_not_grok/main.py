from .ai_manager import AIGenerator


def main() -> None:
    ai_client = AIGenerator()
    try:
        response = ai_client.generate(
            "News of the day in Italy",
            with_web=True,
            show_thinking=False,
        )
        print(response)
    finally:
        ai_client.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .ai_manager import AIGenerator
from .client import DEFAULT_MODEL, DEFAULT_VISION_MODEL, GroqClient, GroqError
from .ui import PickerItem, Theme, UI, human_count, image_data_url, read_text_file, truncated

THEME = Theme("GNG", "🤖", 0, "Groq · web search", badge=True)

ALIASES = {
    "oss120": DEFAULT_MODEL,
    "oss": "openai/gpt-oss-20b",
    "qwen": DEFAULT_VISION_MODEL,
    "vision": DEFAULT_VISION_MODEL,
    "allam": "allam-2-7b",
}
ALIAS_NAMES: dict[str, str] = {}
for _alias, _model in ALIASES.items():
    ALIAS_NAMES.setdefault(_model, _alias)

# Used when the API key is absent or /models cannot be reached.  The live
# /models endpoint remains authoritative for a normal run.
FALLBACK_MODELS = [
    {"id": DEFAULT_MODEL, "owned_by": "OpenAI", "context_window": 131072},
    {"id": "openai/gpt-oss-20b", "owned_by": "OpenAI", "context_window": 131072},
    {"id": DEFAULT_VISION_MODEL, "owned_by": "Alibaba Cloud", "context_window": 131072},
    {"id": "allam-2-7b", "owned_by": "SDAIA", "context_window": 4096},
]
SKIP_MODEL_PREFIXES = ("whisper", "orpheus", "prompt-guard", "safeguard")


def _resolve_model_name(token: str) -> str:
    token = token.strip()
    return ALIASES.get(token.lower(), token)


def _catalog(ai: AIGenerator | None = None) -> tuple[list[dict], str]:
    source = "live"
    try:
        client = ai.client if ai is not None else GroqClient()
        entries = client.list_models()
    except (GroqError, OSError, ValueError):
        entries = FALLBACK_MODELS
        source = "offline"
    choices = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("id")
        if not isinstance(model, str) or not model:
            continue
        if any(marker in model for marker in SKIP_MODEL_PREFIXES):
            continue
        choices.append({
            "id": model,
            "label": model + (f" ({ALIAS_NAMES[model]})" if model in ALIAS_NAMES else ""),
            "owner": str(entry.get("owned_by") or "—"),
            "ctx": entry.get("context_window") or 0,
            "vision": model == DEFAULT_VISION_MODEL or "qwen" in model.lower(),
            "access": "free",
            "source": source,
        })
    if not choices:
        choices = [{
            "id": entry["id"],
            "label": entry["id"],
            "owner": entry["owned_by"],
            "ctx": entry["context_window"],
            "vision": entry["id"] == DEFAULT_VISION_MODEL or "qwen" in entry["id"],
            "access": "free",
            "source": "offline",
        } for entry in FALLBACK_MODELS]
        source = "offline"
    choices.sort(key=lambda choice: (
        0 if choice["id"] == DEFAULT_MODEL else 1 if choice["id"] == DEFAULT_VISION_MODEL else 2,
        choice["id"],
    ))
    return choices, source


def _print_models(ui: UI, current: str, ai: AIGenerator | None = None, access: str | None = None) -> list[dict]:
    choices, source = _catalog(ai)
    if access == "free":
        choices = [choice for choice in choices if choice.get("access") == "free"]
    rows = []
    for index, choice in enumerate(choices, 1):
        mark = ui.accent("●") if choice["id"] == current else " "
        tags = "vision" if choice["vision"] else ""
        rows.append([
            mark,
            ui.dim(str(index)),
            ui.bold(choice["label"]) if choice["id"] == current else choice["label"],
            ui.dim(choice["owner"]),
            ui.dim(human_count(choice["ctx"])),
            ui.dim(tags),
        ])
    footer = f"Current: {current} · switch with /model <number|id>"
    if source == "offline":
        footer += " · offline catalog"
    title = f"Models · {THEME.name}" + (f" · {access}" if access else "")
    ui.table(title, ["", "#", "MODEL", "OWNER", "CTX", "TAGS"], rows,
             align_right=(1, 4), footer=footer)
    return choices


def _pick_model(ui: UI, choices: list[dict], current: str, title: str, access: str | None = None) -> str | None:
    if not choices:
        ui.hint("No models available.")
        return None
    if not ui.interactive:
        _print_models(ui, current, access=access)
        ui.hint("Interactive picker needs a terminal; use /model <number|id>.")
        return None
    items = []
    for choice in choices:
        detail = f"{choice['owner']} · {human_count(choice['ctx'])}"
        if choice["vision"]:
            detail += " · vision"
        items.append(PickerItem(value=choice["id"], label=choice["label"], detail=detail))
    return ui.pick(title, items, current=current)


def _parse_choice(token: str, choices: list[dict]) -> tuple[str, bool]:
    token = token.strip()
    if not token:
        raise ValueError("model name is empty")
    if token.isdigit():
        index = int(token)
        if not 1 <= index <= len(choices):
            raise ValueError(f"model number must be between 1 and {len(choices)}")
        return choices[index - 1]["id"], True
    selected = _resolve_model_name(token)
    ids = {choice["id"] for choice in choices}
    if selected in ids:
        return selected, True
    matches = [choice["id"] for choice in choices if choice["id"].endswith("/" + selected)]
    if len(matches) == 1:
        return matches[0], True
    return selected, False


def _switch_model(ui: UI, token: str, choices: list[dict], state: dict) -> bool:
    try:
        selected, known = _parse_choice(token, choices)
    except ValueError as error:
        ui.error(str(error), prefix="gng")
        return False
    state["model"] = selected
    ai = state.get("ai")
    if ai is not None:
        ai.model = selected
        ai.client.model = selected
    if known:
        ui.success(f"model → {selected}")
    else:
        ui.hint(f"model → {selected} (not in the catalog; the request may fail)")
    return True


def _status(ui: UI, state: dict) -> None:
    ai = state.get("ai")
    rows = [
        ["provider", "Groq"],
        ["model", state["model"]],
        ["vision model", ai.client.vision_model if ai is not None else DEFAULT_VISION_MODEL],
        ["web", "on" if state["web"] else "off"],
        ["history", "on" if state["history"] else "off"],
        ["turns", str(len(state["messages"]))],
    ]
    ui.table("Status", ["SETTING", "VALUE"], rows)


def _show_history(ui: UI, messages: list[dict], enabled: bool) -> None:
    if not messages:
        ui.hint("(history is empty)")
        return
    ui.section("History")
    for message in messages:
        print(f"  {ui.accent(message['role'] + ':')} {truncated(message['content'], 200)}", file=ui.out)
    print(file=ui.out)
    ui.hint(f"history {'on' if enabled else 'off'} · /clear clears it")


def _help(ui: UI, state: dict) -> None:
    ui.commands((
        ("/help", "Show this help."),
        ("/models", "Pick a free model interactively."),
        ("/models-all", "Pick from all available models."),
        ("/model [N|id]", "Show or switch the active model."),
        ("/status", "Show model, vision, web and history state."),
        ("/history [on|off]", "Show the conversation or toggle memory."),
        ("/web [on|off]", "Toggle browser-backed web search; Ctrl+W does too."),
        ("/image PATH [text]", "Send a local image."),
        ("/read FILE", "Send a text file as the next prompt."),
        ("/clear", "Clear conversation history."),
        ("/quit, /exit", "Leave the prompt."),
    ))
    ui.hint(f"Current model: {state['model']} · /models lists free Groq models")


def _ask(ui: UI, state: dict, prompt: str) -> None:
    ai = state["ai"]
    label = "searching" if state["web"] else "thinking"
    try:
        with ui.thinking(label):
            reply = ai.generate(
                prompt,
                with_web=state["web"],
                show_thinking=False,
                history=state["messages"] if state["history"] else None,
            )
        print(reply, file=ui.out)
        if state["history"]:
            state["messages"].extend([
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": reply},
            ])
    except KeyboardInterrupt:
        print("\nInterrupted.", file=ui.out)
    except Exception as error:
        ui.error(str(error), prefix="gng")


def _ask_image(ui: UI, state: dict, path: str, prompt: str) -> None:
    ai = state["ai"]
    try:
        if path.startswith(("https://", "data:image/")):
            image = path
        else:
            image = image_data_url(path)
        with ui.thinking("vision"):
            reply = ai.ask_image(prompt, image)
        print(reply, file=ui.out)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=ui.out)
    except Exception as error:
        ui.error(str(error), prefix="gng")


def _repl(ui: UI, model: str | None = None, web: bool = False) -> int:
    selected = _resolve_model_name(model or os.getenv("GROQ_MODEL") or DEFAULT_MODEL)
    try:
        ai = AIGenerator(model=selected)
    except (GroqError, OSError, ValueError) as error:
        ui.error(str(error), prefix="gng")
        return 1

    state = {
        "ai": ai,
        "model": selected,
        "web": bool(web),
        "history": True,
        "messages": [],
    }

    def toggle_web() -> str:
        state["web"] = not state["web"]
        return f"{ui.dim('web')} {ui.accent('on' if state['web'] else 'off')}"

    commands = (
        "/help", "/models", "/models-all", "/model", "/status", "/web",
        "/history", "/image", "/read", "/clear", "/quit", "/exit",
    )
    ui.banner(
        __version__,
        facts=(
            f"model {state['model']}",
            f"web {'on' if state['web'] else 'off'}",
            "history on",
            "provider Groq",
        ),
        hint="/help · Ctrl+W web · /models switch · Ctrl+D quit",
    )
    try:
        while True:
            try:
                line = ui.readline(
                    ui.prompt(),
                    completions=commands,
                    on_ctrl_w=toggle_web,
                ).strip()
            except EOFError:
                print(file=ui.out)
                break
            except KeyboardInterrupt:
                print(file=ui.out)
                continue
            if not line:
                continue

            if not line.startswith("/"):
                _ask(ui, state, line)
                continue

            command, _, arg = line.partition(" ")
            command = command.lower().strip()
            arg = arg.strip()

            if command in ("/quit", "/exit", "/q"):
                ui.hint("Bye.")
                break
            if command == "/help":
                _help(ui, state)
            elif command in ("/models", "/models-all"):
                show_all = command == "/models-all"
                choices, _ = _catalog(ai)
                if not show_all:
                    choices = [choice for choice in choices if choice.get("access") == "free"]
                if arg:
                    _switch_model(ui, arg, choices, state)
                else:
                    title = f"Models · {THEME.name} · {'all' if show_all else 'free'}"
                    selected_model = _pick_model(
                        ui, choices, state["model"], title, None if show_all else "free"
                    )
                    if selected_model:
                        _switch_model(ui, selected_model, choices, state)
            elif command == "/model":
                if not arg:
                    choices, _ = _catalog(ai)
                    choices = [choice for choice in choices if choice.get("access") == "free"]
                    selected_model = _pick_model(
                        ui, choices, state["model"], f"Models · {THEME.name} · free", "free"
                    )
                    if selected_model:
                        _switch_model(ui, selected_model, choices, state)
                else:
                    choices, _ = _catalog(ai)
                    _switch_model(ui, arg, choices, state)
            elif command == "/status":
                _status(ui, state)
            elif command == "/web":
                value = arg.lower()
                if value in ("on", "enable", "true", "1"):
                    state["web"] = True
                elif value in ("off", "disable", "false", "0"):
                    state["web"] = False
                else:
                    state["web"] = not state["web"]
                ui.success(f"web search {'on' if state['web'] else 'off'}")
            elif command == "/history":
                if arg.lower() in ("on", "off"):
                    state["history"] = arg.lower() == "on"
                    if not state["history"]:
                        state["messages"].clear()
                    ui.success(f"history {'on' if state['history'] else 'off'}")
                else:
                    _show_history(ui, state["messages"], state["history"])
            elif command == "/clear":
                state["messages"].clear()
                ui.success("history cleared")
            elif command == "/read":
                if not arg:
                    ui.error("usage: /read FILE", prefix="gng")
                    continue
                try:
                    text = read_text_file(arg)
                except (OSError, ValueError) as error:
                    ui.error(str(error), prefix="gng")
                    continue
                _ask(ui, state, text)
            elif command == "/image":
                if not arg:
                    ui.error("usage: /image PATH [prompt]", prefix="gng")
                    continue
                path, _, caption = arg.partition(" ")
                _ask_image(ui, state, path, caption.strip() or "Describe this image.")
            else:
                ui.error(f"unknown command: {command} · type /help", prefix="gng")
    finally:
        ai.close()
    return 0


def _oneshot(prompt: str, model: str | None = None, image: str | None = None,
             web: bool = False) -> int:
    selected = _resolve_model_name(model) if model else None
    ai = AIGenerator(model=selected)
    try:
        if image:
            source = image if image.startswith(("https://", "data:image/")) else image_data_url(image)
            print(ai.ask_image(prompt, source))
        else:
            print(ai.generate(prompt, with_web=web, show_thinking=False))
    finally:
        ai.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="gng", description="Groq Not Grok CLI")
    parser.add_argument("prompt_parts", nargs="*", help="Prompt; omit for interactive chat")
    parser.add_argument("-p", "--prompt", help="Prompt; same as the positional form")
    parser.add_argument("-m", "--model", help=f"Groq model (default: {DEFAULT_MODEL})")
    parser.add_argument("--models", action="store_true", help="List free models and exit")
    parser.add_argument("--models-all", action="store_true", help="List every available model and exit")
    parser.add_argument("--image", help="Attach a local image or HTTPS image URL")
    parser.add_argument("--web", action="store_true", help="Search the web before replying")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour")
    parser.add_argument("--version", action="version", version=f"gng {__version__}")
    args = parser.parse_args()

    ui = UI(THEME, enabled=False if args.no_color else None)
    selected = _resolve_model_name(args.model or os.getenv("GROQ_MODEL") or DEFAULT_MODEL)

    if args.models or args.models_all:
        _print_models(ui, selected, access=None if args.models_all else "free")
        return

    prompt = args.prompt
    if prompt is None:
        prompt = " ".join(args.prompt_parts) if args.prompt_parts else None

    try:
        if prompt is not None or args.image:
            code = _oneshot(prompt or "Describe this image.", args.model, args.image, args.web)
        else:
            code = _repl(ui, args.model, args.web)
    except (GroqError, OSError, ValueError) as error:
        ui.error(str(error), prefix="gng")
        code = 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        code = 130
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

from .core.kernel import build_kernel


def main():
    kernel = build_kernel()
    print("Personal AI ready. Type 'exit' to quit.")
    while True:
        message = input("You: ").strip()
        if message.lower() in {"exit", "quit"}:
            break
        print(f"Assistant: {kernel.handle_message(message)}")


if __name__ == "__main__":
    main()


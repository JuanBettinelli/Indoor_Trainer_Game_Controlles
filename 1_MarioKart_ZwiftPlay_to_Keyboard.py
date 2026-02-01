if __name__ == "__main__":
    import asyncio
    import sys
    from zwift_play_to_keyboard import (
        KEY_MAPPING_LEFT,
        KEY_MAPPING_RIGHT,
        run_zwift_play_mapper_forever,
    )

    target_name = "Zwift"
    target_address = None

    if sys.platform != "darwin":
        print("Warning: This script is optimized for macOS.")

    if len(sys.argv) > 1:
        if sys.argv[1] == "--name" and len(sys.argv) > 2:
            target_name = sys.argv[2]
            target_address = None
            print(f"Using name filter: {target_name}")
        else:
            target_address = sys.argv[1]
            print(f"Using explicit address: {target_address}")

    print("\n" + "=" * 60)
    print("KEY MAPPING - LEFT CONTROLLER:")
    print("=" * 60)
    for button, key in KEY_MAPPING_LEFT.items():
        print(f"  {button:20} → {key}")
    print("\n" + "=" * 60)
    print("KEY MAPPING - RIGHT CONTROLLER:")
    print("=" * 60)
    for button, key in KEY_MAPPING_RIGHT.items():
        print(f"  {button:20} → {key}")
    print("=" * 60 + "\n")

    try:
        asyncio.run(
            run_zwift_play_mapper_forever(
                target_name_substr=target_name,
                target_address=target_address,
                rescan_interval_seconds=5.0,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped.")

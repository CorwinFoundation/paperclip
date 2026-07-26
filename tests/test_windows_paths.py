from tools.check_windows_paths import invalid_reason, invalid_tracked_paths


def test_current_tracked_tree_is_windows_compatible() -> None:
    from tools.check_windows_paths import tracked_paths

    assert invalid_tracked_paths(tracked_paths()) == []


def test_rejects_reserved_characters() -> None:
    for character in '<>:"\\|?*':
        assert invalid_reason(f"artifact{character}name.json") is not None


def test_rejects_reserved_device_names_with_or_without_extensions() -> None:
    for component in ("CON", "nul.json", "Com1.txt", "LPT9"):
        assert invalid_reason(component) == "reserved Windows device name"


def test_rejects_trailing_dots_and_spaces() -> None:
    assert invalid_reason("artifact.") == "trailing space or dot"
    assert invalid_reason("artifact ") == "trailing space or dot"


def test_accepts_cross_platform_safe_component() -> None:
    assert invalid_reason("037-standard-cybercrime_intrusion.json") is None

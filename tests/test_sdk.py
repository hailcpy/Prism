import prism_sdk


def test_sdk_version() -> None:
    assert prism_sdk.__version__ == "0.1.0"

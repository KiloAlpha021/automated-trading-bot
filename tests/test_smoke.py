def test_package_import():
    assert __import__("automated_trading_bot") is not None

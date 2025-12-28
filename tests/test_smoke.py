def test_app_imports():
    import os
    import sys

    repo_root = os.path.dirname(os.path.dirname(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from main import app
    assert app is not None

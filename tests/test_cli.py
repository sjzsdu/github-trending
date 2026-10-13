"""CLI 路由与参数校验测试（不触网、不启动浏览器）。"""

import pytest

from ghtrend.cli import PLATFORMS, build_parser, effective_interval, main, validate_args


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_parse_platform_action():
    a = _parse("juejin", "login", "--account", "main")
    assert a.platform == "juejin"
    assert a.action == "login"
    assert a.account == "main"


def test_parse_publish_with_article():
    a = _parse("juejin", "publish", "--article", "artifacts/x.md", "--mode", "publish")
    assert a.article == "artifacts/x.md"
    assert a.mode == "publish"


def test_parse_run():
    a = _parse("run", "--limit", "2", "--platforms", "juejin,toutiao", "--mode", "draft")
    assert a.platform == "run"
    assert a.action is None
    assert a.limit == 2
    assert a.platforms == "juejin,toutiao"


def test_parse_defaults():
    a = _parse("juejin", "check")
    assert a.account == "default"


def test_platform_requires_action():
    err = validate_args(_parse("juejin"))
    assert err and "login|check|publish" in err


def test_unknown_platform_rejected():
    err = validate_args(_parse("zhihu", "login"))
    assert err and "未知平台" in err


def test_run_rejects_action():
    err = validate_args(_parse("run", "login"))
    assert err and "run" in err


def test_publish_requires_article_or_latest():
    err = validate_args(_parse("juejin", "publish"))
    assert err and "--article" in err
    assert validate_args(_parse("juejin", "publish", "--latest")) is None


def test_action_choices_enforced():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["juejin", "fly"])


def test_platforms_registry():
    assert "juejin" in PLATFORMS


def test_toutiao_stub_degrades_cleanly(capsys):
    """toutiao stub 不得抛 traceback：三个动作都干净降级 exit 2。"""
    for action in ("check", "login", "publish"):
        argv = ["toutiao", action, "--account", "main"]
        if action == "publish":
            argv += ["--latest"]
        code = main(argv)
        out = capsys.readouterr()
        assert code == 2, f"{action} exit code"
        assert "尚未接入" in out.err
        assert "Traceback" not in out.err


def test_effective_interval_floored_at_300():
    assert effective_interval({"publish": {"interval_seconds": 60}}) == 300
    assert effective_interval({"publish": {"interval_seconds": 299}}) == 300
    assert effective_interval({"publish": {"interval_seconds": 600}}) == 600
    assert effective_interval({}) == 300  # 缺省也保底


def test_effective_interval_used_by_run_wiring():
    """cmd_run 的间隔取自 effective_interval（防止回归为裸读配置）。"""
    import inspect
    from ghtrend import cli

    assert "effective_interval(cfg)" in inspect.getsource(cli.cmd_run)

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
    err = validate_args(_parse("wechat", "login"))
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


def test_all_platforms_instantiated(capsys):
    """四个平台 adapter 都已接入（不再是 stub），CLI 不会因 NotImplementedError 崩溃。"""
    from ghtrend.publish import ADAPTERS
    for platform in ("juejin", "toutiao", "csdn", "zhihu"):
        cls = ADAPTERS.get(platform)
        assert cls is not None, f"{platform} 未注册"
        argv = [platform, "check", "--account", "main"]
        code = main(argv)
        out = capsys.readouterr()
        # 未登录（profile 不存在）时 exit code 1，不会抛 NotImplementedError / 未知平台
        assert "Traceback" not in out.err, f"{platform} 抛了 traceback"


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

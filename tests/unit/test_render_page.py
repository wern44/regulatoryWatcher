"""Tests for the render_page helper that auto-injects sidebar_badges."""
from unittest.mock import MagicMock

from regwatch.services.sidebar_badges import SidebarBadges


def test_render_page_injects_sidebar_badges_into_context(monkeypatch):
    from regwatch.web import templates_context

    captured: dict = {}

    class FakeTemplates:
        def TemplateResponse(self, request, template_name, context):  # noqa: N802
            captured["request"] = request
            captured["template_name"] = template_name
            captured["context"] = context
            return "rendered"

    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=fake_session),
        __exit__=MagicMock(return_value=False),
    ))

    fake_request = MagicMock()
    fake_request.app.state.templates = FakeTemplates()
    fake_request.app.state.session_factory = fake_session_factory

    fake_badges = SidebarBadges(
        inbox=2, catalog=0, ict=1, drafts=0, deadlines=3,
    )

    fake_service = MagicMock()
    fake_service.counts.return_value = fake_badges
    monkeypatch.setattr(
        templates_context, "SidebarBadgeService",
        MagicMock(return_value=fake_service),
    )

    out = templates_context.render_page(
        fake_request, "x.html", {"foo": "bar"}
    )

    assert out == "rendered"
    assert captured["template_name"] == "x.html"
    assert captured["context"]["foo"] == "bar"
    assert captured["context"]["sidebar_badges"] is fake_badges


def test_render_page_does_not_overwrite_caller_supplied_sidebar_badges(monkeypatch):
    """Defensive: if a caller passes sidebar_badges explicitly, do not override."""
    from regwatch.web import templates_context

    captured: dict = {}

    class FakeTemplates:
        def TemplateResponse(self, request, template_name, context):  # noqa: N802
            captured["context"] = context
            return None

    fake_session = MagicMock()
    fake_request = MagicMock()
    fake_request.app.state.templates = FakeTemplates()
    fake_request.app.state.session_factory = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=fake_session),
            __exit__=MagicMock(return_value=False),
        ),
    )

    monkeypatch.setattr(
        templates_context, "SidebarBadgeService",
        MagicMock(return_value=MagicMock(counts=MagicMock(return_value="default"))),
    )

    explicit = SidebarBadges(inbox=99, catalog=0, ict=0, drafts=0, deadlines=0)
    templates_context.render_page(
        fake_request, "x.html", {"sidebar_badges": explicit}
    )

    assert captured["context"]["sidebar_badges"] is explicit

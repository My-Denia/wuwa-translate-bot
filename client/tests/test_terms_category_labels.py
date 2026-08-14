"""结果表里的分类必须是中文,而且未知分类要退化成原值。

这是「字没经过 strings.py」这个洞的第三个出口,前两个是 Qt 自带部件的文案
(标准按钮、输入框右键菜单,见 test_qt_translations.py)。这一个的来源不同:
分类是**服务端数据里的值**,由 API 原样带过来,一次也不经过这个程序的字面量,
所以静态门看不见它,Qt 翻译也管不着它 —— 改版做完、套件全绿、Qt 文案也修好
之后,这一列仍然是 system / echo / weapon,是逐屏看截图才发现的。

断的是**渲染出来的单元格**,不是那张映射表。映射表可以写得很全而没有人调用,
那样断表会绿而界面仍是英文;断单元格对这种失败一视同仁。
"""

from __future__ import annotations

import re

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
from wuwaterm_client.api import ApiClient, TermMatch, TermsResult  # noqa: E402
from wuwaterm_client.ui import terms_view  # noqa: E402
from wuwaterm_client.ui.components import apply_credential_backend  # noqa: E402
from wuwaterm_client.ui.terms_view import TermsView  # noqa: E402

CATEGORY_COLUMN = 2
CJK = re.compile(r"[一-鿿]")

# 服务端建库时使用的全部分类,取自 src/wuwaterm/constants.py 的 CATEGORY_ORDER
# 与 src/wuwaterm/builder.py 的 CategorySpec。这里重复一份是有意的:它是一份
# 契约快照,服务端加了分类而客户端没跟上时,下面那条测试会红。
SERVICE_CATEGORIES = (
    "core_term",
    "resonator",
    "weapon",
    "echo",
    "skill",
    "sonata_effect",
    "location",
    "item",
    "speaker",
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _view() -> TermsView:
    async def handler(request):  # pragma: no cover - 这个文件不发请求
        raise AssertionError("rendering tests send nothing")

    return TermsView(
        ApiClient("https://test", _test_transport=httpx.MockTransport(handler))
    )


def _rendered(view: TermsView, categories) -> list[str]:
    result = TermsResult(
        query="鸣式",
        matches=tuple(
            TermMatch(zh="词", en="term", category=category, score=90.0, reason="exact")
            for category in categories
        ),
        request_id="rid",
    )
    view._render_result(result)
    return [
        view.table.item(row, CATEGORY_COLUMN).text()
        for row in range(view.table.rowCount())
    ]


def test_every_service_category_renders_in_chinese(qapp) -> None:
    """九个分类逐个过一遍表格,出来的必须是中文。"""
    texts = _rendered(_view(), SERVICE_CATEGORIES)

    assert len(texts) == len(SERVICE_CATEGORIES)
    for category, text in zip(SERVICE_CATEGORIES, texts):
        assert text != category, f"分类 {category!r} 仍然以原始英文渲染"
        assert CJK.search(text), f"分类 {category!r} 渲染成了 {text!r},其中没有中文"


def test_an_unknown_category_degrades_to_the_service_value(qapp) -> None:
    """服务端加一个这个版本没听过的分类时,界面要退化成可读的英文。

    空白单元格会让那条词条看起来没有分类,占位符则什么也没说;显示服务端给的
    原值是唯一一种「一眼能看出该更新客户端了」的退化。
    """
    texts = _rendered(_view(), ("a_category_from_the_future",))

    assert texts == ["a_category_from_the_future"]


def test_the_label_table_covers_the_service_categories() -> None:
    """客户端的映射表与服务端的分类集合逐项相等。

    双向相等:少一个是界面上冒出英文,多一个是给一个不存在的分类留了译名 ——
    后者不会被用户看见,但它会让人以为覆盖是完整的。

    映射表在视图层(terms_view),文案在 strings —— 与 _KIND_LABELS、
    _REASON_LABELS 同一形状,见 strings.py 模块开头对这条约定的说明。
    """
    assert set(terms_view._CATEGORY_LABELS) == set(SERVICE_CATEGORIES)
    for value in terms_view._CATEGORY_LABELS.values():
        assert CJK.search(value), f"分类译名 {value!r} 里没有中文"


def test_the_credential_backend_row_reads_in_chinese(qapp) -> None:
    """凭据存储后端是第二处「服务端/库给的取值」直接上屏的地方。

    与分类同一类缺陷:它是 keyring 的类名,不经过任何 setText 的字面量检查。
    这里断的同样是**渲染结果**——标签上的字,以及未知后端退化成原始类名。
    """
    from PySide6.QtWidgets import QLabel

    label = QLabel()
    apply_credential_backend(label, "WinVaultKeyring")
    assert label.text() == strings.CREDENTIAL_BACKEND_WINDOWS
    assert CJK.search(label.text())
    # 类名没有丢,只是从正文挪到了悬停里。
    assert "WinVaultKeyring" in label.toolTip()

    apply_credential_backend(label, "SomeFutureKeyring")
    assert label.text() == "SomeFutureKeyring"


def test_the_score_bar_survives_a_non_finite_value(qapp) -> None:
    """部件层的第二道底:min/max 会**传播** NaN 而不是把它夹住。

    解析层已经拒绝了非有限值(见 test_error_dispatch),这一条挡的是任何其他
    途径送进来的 NaN —— 一个画不出数字的部件应当画一根空条,而不是把窗口带走。
    """
    import math

    from wuwaterm_client.ui.components import SCORE_MINIMUM, ScoreBar

    bar = ScoreBar()
    for bad in (float("nan"), float("inf"), float("-inf")):
        bar.set_score(bad)
        assert math.isfinite(bar.score())
    bar.set_score(float("nan"))
    assert bar.score() == SCORE_MINIMUM

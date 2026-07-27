from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = (
    Path.home()
    / ".codex"
    / "skills"
    / "guobu-examine-api-collector"
    / "scripts"
    / "collect_guobu_examine_api_from_edge.ps1"
)
ONE_CLICK = PROJECT_ROOT / "tools" / "guobu_one_click_collect.js"


def test_collector_enriches_list_records_with_detail_goods_photo():
    script = COLLECTOR.read_text(encoding="utf-8-sig")

    assert 'fetch("/api/cellPhone/26/apply/detail"' in script
    assert "goodsPhoto" in script
    assert "$titleProduct" in script
    assert "$imageGroups[$titleProduct]" in script


def test_collector_reports_detail_and_required_photo_group_failures():
    script = COLLECTOR.read_text(encoding="utf-8-sig")

    assert "goods_count" in script
    assert "detail_error" in script
    assert "missing_required_photo_group_count" in script


def test_collector_persists_chinese_category_name_for_model():
    script = COLLECTOR.read_text(encoding="utf-8-sig")

    assert "$($record.cateCodeName)" in script
    assert "cate_code_name = $record.cateCodeName" in script
    assert "$categoryText" in script


def test_project_collector_entry_is_one_click_wrapper():
    script = ONE_CLICK.read_text(encoding="utf-8")

    assert "collect_guobu_filtered.ps1" in script
    assert "-SkipPageFilter" in script
    assert "expectTotal" in script

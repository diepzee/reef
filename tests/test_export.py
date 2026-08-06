from rif.access import Principal
from rif.export import export_space, render_page
from rif.pages import get_page, save_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_render_round_trips_through_the_import_parser(tx, household):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts")))
    from import_mark import parse_markdown

    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "house.md", "boiler", message="x", title="House", tags=["home"]
    )
    meta, body = parse_markdown(
        render_page(await get_page(me, "household", "house.md"))
    )
    assert meta["title"] == "House" and meta["tags"] == ["home"]
    assert body.strip() == "boiler"


async def test_export_writes_one_file_per_page(tx, household, tmp_path):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "boiler", message="x")
    await save_page(me, "household", "money.md", "loan", message="x")
    assert await export_space(me, "household", tmp_path) == 2
    assert "loan" in (tmp_path / "money.md").read_text()

from tools.import_server_reports import _registry_ids, _remap_items


def test_registry_ids_strip_namespace_and_preserve_protocol_ids():
    reports = {
        "minecraft:item": {
            "entries": {
                "minecraft:stone": {"protocol_id": 1},
                "minecraft:diamond_pickaxe": {"protocol_id": 966},
            }
        }
    }

    assert _registry_ids(reports, "item") == {
        "stone": 1,
        "diamond_pickaxe": 966,
    }


def test_item_remap_keeps_metadata_while_using_report_ids():
    base = [{"id": 939, "name": "diamond_pickaxe", "stackSize": 1}]

    assert _remap_items(base, {"diamond_pickaxe": 966}) == [{
        "id": 966,
        "name": "diamond_pickaxe",
        "stackSize": 1,
    }]

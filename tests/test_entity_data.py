from amp.entity_data import entity_name


def test_supported_entity_registry_exposes_readable_names():
    assert entity_name("26.1.2", 150) == "zombie"
    assert entity_name("26.1.2", 9999) == "entity_9999"

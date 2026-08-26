from entity_data import entity_name


def test_1202_entity_registry_exposes_readable_names():
    assert entity_name("1.20.2", 118) == "zombie"
    assert entity_name("1.20.2", 9999) == "entity_9999"

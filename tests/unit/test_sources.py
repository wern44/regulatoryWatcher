from regwatch.pipeline.sources import SOURCE_GROUP_LABELS, SOURCE_GROUPS


def test_source_groups_cover_all_known_sources():
    all_grouped = set()
    for names in SOURCE_GROUPS.values():
        all_grouped.update(names)
    expected = {
        "cssf_rss", "cssf_consultation",
        "eur_lex_adopted", "eur_lex_proposal",
        "legilux_sparql", "legilux_parliamentary",
        "esma_rss", "eba_rss", "ec_fisma_rss",
    }
    assert expected == all_grouped


def test_source_group_labels_match_groups():
    assert set(SOURCE_GROUP_LABELS.keys()) == set(SOURCE_GROUPS.keys())

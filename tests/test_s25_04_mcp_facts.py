"""S25-04 fixed-catalog MCP answer fidelity positive/negative cases."""
from agent.mock_mcp import MockMCPClient
from evals.s25_04_mcp_facts import mcp_definition_faithful


ENTRY = MockMCPClient().call("mcp_reference_lookup", {"term": "MCP"})


def check(answer):
    return mcp_definition_faithful(answer, ENTRY["definition"], ENTRY["source"])


def test_chinese_paraphrase_with_source_is_accepted():
    assert check("MCP（Model Context Protocol）是一种用于连接工具和数据源的协议。来源：trusted_mock_catalog_v1")


def test_exact_english_definition_with_source_is_accepted():
    assert check(ENTRY["definition"] + " Source: " + ENTRY["source"])


def test_omitting_data_sources_is_not_accepted():
    assert not check("MCP (Model Context Protocol) is a protocol connecting tools. Source: trusted_mock_catalog_v1")


def test_omitting_source_is_not_accepted():
    assert not check("MCP (Model Context Protocol) is a protocol connecting tools and data sources.")


def test_unrelated_statement_with_source_is_not_accepted():
    assert not check("MCP (Model Context Protocol) is a protocol for predicting weather. Source: trusted_mock_catalog_v1")


def test_unrecognized_fixture_definition_fails_closed():
    assert not mcp_definition_faithful("MCP (Model Context Protocol) connects tools and data sources. Source: trusted_mock_catalog_v1", "unrecognized definition", ENTRY["source"])

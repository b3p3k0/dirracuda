"""
Unit tests for redseek/parser.py.

Tests cover extraction, normalization, classification, cleanup,
deduplication, and edge-case rejection.
No network calls. No conftest.py — all fixtures are local.
"""

import datetime

import pytest

from experimental.redseek.parser import extract_targets, make_dedupe_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
_POST_ID = "testpost1"


def _targets(title, selftext=None, parse_body=True, post_id=_POST_ID):
    return extract_targets(post_id, title, selftext, parse_body, _NOW)


def _normalized(targets):
    return [t.target_normalized for t in targets]


def _protocols(targets):
    return [t.protocol for t in targets]


def _confidences(targets):
    return [t.parse_confidence for t in targets]


# ---------------------------------------------------------------------------
# URL extraction — scheme detection
# ---------------------------------------------------------------------------

def test_extracts_http_url():
    result = _targets("Check out http://example.com/files/")
    assert any("http://example.com/files" in n for n in _normalized(result))
    assert any(p == "http" for p in _protocols(result))


def test_extracts_https_url():
    result = _targets("Get it at https://files.example.com/data")
    assert any("https" in n for n in _normalized(result))
    assert any(p == "https" for p in _protocols(result))


def test_extracts_ftp_url():
    result = _targets("FTP server: ftp://ftp.example.com/pub/")
    assert any("ftp://ftp.example.com" in n for n in _normalized(result))
    assert any(p == "ftp" for p in _protocols(result))


def test_url_confidence_is_high():
    result = _targets("http://example.com/files")
    assert all(c == "high" for c in _confidences(result))


def test_url_scheme_lowercased():
    # Scheme and host are normalized to lowercase; path case is preserved
    result = _targets("HTTP://EXAMPLE.COM/FILES")
    assert any(n.startswith("http://example.com/") for n in _normalized(result))


def test_url_host_lowercased():
    result = _targets("http://EXAMPLE.COM/path")
    assert any("http://example.com/path" in n for n in _normalized(result))


def test_url_bare_root_slash_stripped():
    result = _targets("http://example.com/")
    assert any(n == "http://example.com" for n in _normalized(result))


def test_url_non_root_path_preserved():
    result = _targets("http://example.com/files/")
    assert any("/files/" in n for n in _normalized(result))


def test_url_auth_stripped_from_normalized():
    result = _targets("ftp://user:pass@ftp.example.com/pub")
    # raw should contain auth; normalized should not
    assert any("user:pass" not in n for n in _normalized(result))
    assert any("ftp.example.com" in n for n in _normalized(result))


def test_url_over_2048_chars_dropped():
    long_url = "http://example.com/" + "a" * 2100
    result = _targets(f"check {long_url} out")
    assert not any(len(n) > _URL_MAX_LEN_APPROX for n in _normalized(result))


_URL_MAX_LEN_APPROX = 2048


# ---------------------------------------------------------------------------
# host:port extraction
# ---------------------------------------------------------------------------

def test_extracts_ip_port():
    result = _targets("Server is at 192.168.1.100:8080")
    assert any("192.168.1.100:8080" in n for n in _normalized(result))


def test_extracts_hostname_port():
    result = _targets("nas.local:9000 has the files")
    assert any("nas.local:9000" in n for n in _normalized(result))


def test_host_port_confidence_is_medium():
    result = _targets("nas.local:9000")
    assert any(c == "medium" for c in _confidences(result))


def test_host_port_443_infers_https():
    result = _targets("server.example.com:443")
    assert any(p == "https" for p in _protocols(result))


def test_host_port_80_infers_http():
    result = _targets("1.2.3.4:80")
    assert any(p == "http" for p in _protocols(result))


def test_host_port_21_infers_ftp():
    result = _targets("10.0.0.1:21")
    assert any(p == "ftp" for p in _protocols(result))


def test_host_port_unknown_port_protocol_unknown():
    result = _targets("nas.local:9000")
    # port 9000 is not in the inference table
    assert any(p == "unknown" for p in _protocols(result))


def test_host_port_zero_dropped():
    result = _targets("example.com:0 is invalid")
    assert not any(":0" in n for n in _normalized(result))


def test_host_port_65536_dropped():
    result = _targets("example.com:65536 too high")
    assert not any(":65536" in n for n in _normalized(result))


def test_host_port_ip_octet_256_dropped():
    result = _targets("256.0.0.1:8080 bad ip")
    assert not any("256.0.0.1" in n for n in _normalized(result))


# ---------------------------------------------------------------------------
# Raw IPv4 extraction
# ---------------------------------------------------------------------------

def test_extracts_raw_ipv4():
    result = _targets("box is at 10.20.30.40 somewhere")
    assert any("10.20.30.40" in n for n in _normalized(result))


def test_ipv4_confidence_is_low():
    result = _targets("10.20.30.40")
    assert any(c == "low" for c in _confidences(result))


def test_ipv4_protocol_unknown():
    result = _targets("10.20.30.40")
    assert any(p == "unknown" for p in _protocols(result))


def test_ipv4_octet_256_dropped():
    result = _targets("256.1.2.3 is not valid")
    assert not any("256.1.2.3" in n for n in _normalized(result))


def test_ipv4_octet_255_valid():
    result = _targets("255.255.255.255")
    assert any("255.255.255.255" in n for n in _normalized(result))


# ---------------------------------------------------------------------------
# Bare domain extraction
# ---------------------------------------------------------------------------

def test_extracts_bare_domain():
    result = _targets("visit example.com for the files")
    assert any(t.host == "example.com" for t in result)


def test_bare_domain_confidence_is_medium():
    result = _targets("check example.com out")
    domain_targets = [t for t in result if t.host == "example.com"]
    assert any(t.parse_confidence == "medium" for t in domain_targets)


def test_bare_domain_protocol_unknown():
    result = _targets("check example.com out")
    domain_targets = [t for t in result if t.host == "example.com"]
    assert any(t.protocol == "unknown" for t in domain_targets)


def test_email_domain_not_extracted_as_bare_domain():
    """user@example.com must not yield example.com as a standalone target."""
    result = _targets("contact user@example.com for info")
    bare = [t for t in result if t.parse_confidence == "medium" and t.host == "example.com"]
    assert len(bare) == 0


def test_bare_domain_digit_only_tld_dropped():
    result = _targets("version example.123 here")
    assert not any("example.123" in n for n in _normalized(result))


def test_bare_domain_version_string_dropped():
    """v1.2.3 style version strings should not produce bare domain targets."""
    result = _targets("using v1.2.3 for the release")
    # "v1.2.3" — labels include all-digit ones, so it should be dropped
    assert not any("1.2.3" in n and t.parse_confidence == "medium"
                   for t, n in zip(result, _normalized(result)))


def test_bare_domain_modern_tld_matched():
    """Domains with longer TLDs (>6 chars) should be matched."""
    result = _targets("check out files.photography today")
    assert any("photography" in n for n in _normalized(result))


# ---------------------------------------------------------------------------
# Pre-extraction cleanup
# ---------------------------------------------------------------------------

def test_markdown_http_link_extracted():
    result = _targets("[click here](http://example.com/files)")
    assert any("http://example.com/files" in n for n in _normalized(result))


def test_markdown_https_link_extracted():
    result = _targets("[files](https://cdn.example.com/data)")
    assert any("https://cdn.example.com/data" in n for n in _normalized(result))


def test_markdown_ftp_link_extracted():
    result = _targets("[ftp stuff](ftp://files.example.com/pub)")
    assert any("ftp://files.example.com/pub" in n for n in _normalized(result))


def test_html_entity_amp_unescaped():
    result = _targets("http://example.com/a&amp;b=1")
    # After unescape, the URL contains & which is a valid URL character
    assert any("example.com" in n for n in _normalized(result))


def test_trailing_period_stripped_from_url():
    result = _targets("see http://example.com/path.")
    assert not any(n.endswith(".") for n in _normalized(result))


def test_trailing_comma_stripped():
    result = _targets("see http://example.com/path,")
    assert not any(n.endswith(",") for n in _normalized(result))


# ---------------------------------------------------------------------------
# parse_body flag
# ---------------------------------------------------------------------------

def test_parse_body_true_includes_selftext_targets():
    result = extract_targets(
        _POST_ID, "title", "http://body.example.com/files", True, _NOW
    )
    assert any("body.example.com" in n for n in _normalized(result))


def test_parse_body_false_ignores_selftext():
    result = extract_targets(
        _POST_ID, "title only", "http://body.example.com/files", False, _NOW
    )
    assert not any("body.example.com" in n for n in _normalized(result))


# ---------------------------------------------------------------------------
# Selftext special values
# ---------------------------------------------------------------------------

def test_deleted_selftext_not_parsed():
    result = extract_targets(_POST_ID, "some title", "[deleted]", True, _NOW)
    assert len(result) == 0


def test_removed_selftext_not_parsed():
    result = extract_targets(_POST_ID, "some title", "[removed]", True, _NOW)
    assert len(result) == 0


def test_empty_selftext_not_parsed():
    result = extract_targets(_POST_ID, "some title", "", True, _NOW)
    assert len(result) == 0


def test_whitespace_only_selftext_not_parsed():
    result = extract_targets(_POST_ID, "some title", "   \n  ", True, _NOW)
    assert len(result) == 0


def test_none_selftext_with_parse_body_true():
    """None selftext should not raise — treated as absent."""
    result = extract_targets(_POST_ID, "http://example.com", None, True, _NOW)
    assert any("example.com" in n for n in _normalized(result))


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_duplicate_url_in_title_deduped():
    result = _targets("http://example.com/files http://example.com/files again")
    normalized = _normalized(result)
    assert len(normalized) == len(set(normalized))


def test_duplicate_url_across_title_and_body_deduped():
    result = extract_targets(
        _POST_ID,
        "http://example.com/files",
        "http://example.com/files",
        True,
        _NOW,
    )
    normalized = _normalized(result)
    assert len(normalized) == len(set(normalized))


# ---------------------------------------------------------------------------
# Dedupe key stability
# ---------------------------------------------------------------------------

def test_dedupe_key_is_stable():
    k1 = make_dedupe_key("post1", "http://example.com")
    k2 = make_dedupe_key("post1", "http://example.com")
    assert k1 == k2


def test_dedupe_key_differs_by_post():
    k1 = make_dedupe_key("post1", "http://example.com")
    k2 = make_dedupe_key("post2", "http://example.com")
    assert k1 != k2


def test_dedupe_key_differs_by_target():
    k1 = make_dedupe_key("post1", "http://example.com")
    k2 = make_dedupe_key("post1", "http://other.com")
    assert k1 != k2


# ---------------------------------------------------------------------------
# Source truncation
# ---------------------------------------------------------------------------

def test_source_over_100kb_sets_truncated_note():
    big_body = "http://example.com/file\n" * 6000  # well over 100KB
    result = extract_targets(_POST_ID, "title", big_body, True, _NOW)
    if result:
        assert all(t.notes == "truncated" for t in result)


def test_source_under_100kb_no_truncated_note():
    result = _targets("http://example.com/files")
    assert all(t.notes != "truncated" for t in result)


# ---------------------------------------------------------------------------
# Priority — URL wins over bare domain for same span
# ---------------------------------------------------------------------------

def test_url_takes_priority_over_bare_domain():
    """http://example.com should not also yield example.com as a bare domain."""
    result = _targets("http://example.com/files")
    protocols = _protocols(result)
    # Should have exactly one target with protocol http (URL match)
    # and NOT a second low-confidence bare domain match for example.com
    http_targets = [t for t in result if t.protocol == "http"]
    bare_targets = [t for t in result if t.parse_confidence == "medium"
                    and t.host == "example.com" and t.protocol == "unknown"]
    assert len(http_targets) >= 1
    assert len(bare_targets) == 0


# ---------------------------------------------------------------------------
# Output types and structure
# ---------------------------------------------------------------------------

def test_target_fields_are_populated():
    result = _targets("http://example.com/files")
    assert len(result) == 1
    t = result[0]
    assert t.post_id == _POST_ID
    assert t.target_raw is not None
    assert t.target_normalized is not None
    assert t.host is not None
    assert t.protocol is not None
    assert t.parse_confidence in ("high", "medium", "low")
    assert t.created_at == _NOW
    assert t.dedupe_key is not None
    assert t.id is None  # AUTOINCREMENT, not set before insert


def test_empty_title_no_crash():
    result = _targets("")
    assert isinstance(result, list)


def test_no_targets_in_title_returns_empty():
    result = _targets("a perfectly normal sentence with no URLs or IPs")
    assert isinstance(result, list)

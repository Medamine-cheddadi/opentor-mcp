---
date: 2026-08-13
topic: opentor-mcp-improvements
---

# OpenTor MCP Improvements

## Problem Frame

OpenTor MCP (v0.1.0) provides 27 tools for AI-assisted Tor browsing, but users experience friction across three axes:

1. **Reliability** — JavaScript-heavy pages don't render, extraction heuristics miss content, and failures provide little recovery guidance. The AI assistant ends up retrying blindly.
2. **Capability gaps** — Single-page browsing, no file downloads, limited form interaction, and basic session management constrain what research workflows are possible.
3. **Usability** — Common tasks (paginated research, site crawling, structured data collection) require many sequential tool calls that the AI must orchestrate manually, leading to slow and error-prone workflows.

These compound: unreliable primitives make multi-step workflows fragile, and missing capabilities force workarounds that add more steps. The improvement strategy phases work in dependency order: reliable foundations → new capabilities → intelligent composition.

## Requirements

### Phase 1 — Foundation (Reliability)

- R1. **Smart page waiting.** Navigation and interaction tools must support configurable wait strategies — network idle, specific element visible, custom timeout — so JavaScript-rendered content loads before extraction. The default behavior should be intelligent (wait for network idle with a reasonable timeout) without requiring explicit configuration for common cases.
- R2. **Robust extraction fallbacks.** Content extraction must use a fallback chain when the primary strategy produces poor results (e.g., empty or very short output). The chain should attempt multiple strategies in order and select the best result. Markdown conversion must handle tables, nested lists, and embedded media better than the current implementation.
- R3. **Self-healing navigation.** When navigation fails due to Tor circuit issues (timeout, connection reset), the MCP should automatically rotate circuits and retry before surfacing the error. The retry budget and behavior must be configurable, with a safe default maximum retry count and exponential backoff. Circuit rotation should not discard session cookies unless the user explicitly requests a new identity.
- R4. **Rich error context.** Error responses must include actionable guidance — what likely went wrong, what the AI should try next, and whether retrying is likely to help. Errors should distinguish between transient failures (retry-worthy) and permanent failures (element doesn't exist, URL blocked by policy). Error messages must never echo back values submitted to password or other sensitive form fields.

### Phase 2 — Platform (New Capabilities)

- R5. **Multi-tab browsing.** Support opening, switching between, and closing multiple browser tabs. Each tab maintains its own page state. Tools that operate on page content must accept an optional tab identifier; when omitted, they operate on the active tab. Tab count must be bounded to prevent resource exhaustion.
- R6. **File downloads.** Support downloading files discovered on Tor pages. Downloads must enforce size limits, allowed MIME type filtering, and must be saved to a configurable local directory. Download progress and completion must be reported. Downloaded filenames must be safely derived (sanitized to a single path component, no traversal sequences or symlink targets) using the same symlink-safe, owner-only-permission write pattern used for archives and sessions. The URL policy gate must apply to download URLs.
- R7. **Form interaction helpers.** Provide higher-level form tools: batch field filling (fill multiple fields in one call), dropdown/select interaction, and checkbox/radio toggling. These reduce the number of tool calls needed for login flows and multi-field forms from N calls to 1.
- R8. **Enhanced session management.** Sessions should support named profiles that bundle cookies with metadata (site URL, creation date, last used, description). Sessions should optionally auto-save on significant navigation events. Loading a session should restore to the associated site URL.

### Phase 3 — Intelligence (Smart Composition)

- R9. **Structured data extraction.** Provide a tool that accepts a user-defined schema (field names + CSS/XPath selectors or descriptions) and returns structured JSON matching that schema. This eliminates the pattern of "read page → parse markdown → extract data" that currently requires multiple calls and fragile text parsing.
- R10. **Auto-pagination.** Provide a tool that follows "next page" links automatically, extracts content from each page, and returns aggregated results. Must support configurable page limits, duplicate detection, and different pagination patterns (numbered pages, "load more" buttons, infinite scroll).
- R11. **Content-type detection.** Automatically classify pages (article, forum, search results, login form, directory listing, error page) and apply the best extraction strategy without the user specifying it. The detected type should be included in page metadata so the AI can adapt its approach.
- R12. **Workflow tools.** Provide higher-level research primitives:
  - *Bounded site crawl* — crawl a site following same-origin internal links (same scheme+host as the starting URL) up to a configurable depth and page limit, returning a site map with page summaries. Cross-origin links are excluded from traversal.
  - *Page monitoring* — detect content changes on a page between visits, returning a structured diff.
  - *Page comparison* — compare content across two URLs (potentially in different tabs) and return structured differences.

## Success Criteria

- **Phase 1:** Existing test scenarios that currently require manual retries or produce poor extraction results work reliably on first attempt. Error messages are actionable (a reviewer can determine what to do next from the error alone).
- **Phase 2:** A multi-page research workflow (open 3 tabs, fill a login form, download a document) completes in significantly fewer tool calls than the equivalent workflow today. Session restore returns to a working authenticated state.
- **Phase 3:** Extracting a structured dataset from a paginated listing completes in 1-2 tool calls instead of 10+. The AI does not need to specify extraction strategy for common page types.

## Scope Boundaries

- **Not a Tor Browser replacement.** No attempt to replicate Tor Browser's fingerprint or provide anonymity guarantees. This remains a research tool for a trusted local operator.
- **No autonomous crawling.** Workflow tools (R12) are bounded and operator-initiated, not autonomous agents. Crawl depth and page limits are mandatory parameters, not optional.
- **No proxy management.** The MCP continues to require an externally running Tor daemon. Managing Tor lifecycle is out of scope.
- **No browser extension / GUI.** All interaction remains through MCP tool calls. No visual browser UI is exposed.
- **Single operator model.** Multi-tab (R5) serves one operator's parallel research, not concurrent users.

## Key Decisions

- **Three-phase approach over shotgun:** Improvements are sequenced Foundation → Platform → Intelligence because each phase depends on the previous. Reliable page loading (R1) is prerequisite for multi-tab (R5), which is prerequisite for page comparison (R12).
- **Smart defaults over configuration:** Phase 1 tools should "just work" with intelligent defaults (R1 smart waiting, R2 fallback chains) rather than requiring the user to specify strategies. Configuration is available but not required.
- **Higher-level tools over more primitives:** Phase 3 prioritizes tools that compose existing capabilities (R9-R12) rather than exposing more low-level browser APIs. Fewer, smarter tool calls > many granular ones.
- **Circuit rotation preserves sessions by default (R3):** `rotate_circuit()` already exists and preserves cookies. Self-healing retries should use this, not `new_identity()`, to avoid breaking authenticated sessions.

## Dependencies / Assumptions

- **Playwright multi-page support.** Multi-tab (R5) builds on the existing pattern in `check_tor_connection()`, which already creates a second `Page` within the shared `BrowserContext`. The open design questions are tab lifecycle management and rethinking the serialization lock, not whether multi-page works through the Tor proxy.
- **File download interception.** File downloads (R6) assume Playwright's download event handling works through a SOCKS5 proxy. Needs verification.
- **Current architecture supports Phase 1 changes.** The `serialized_browser_tool` lock and single-context model are sufficient for Phase 1. Multi-tab (R5) will require rethinking the locking strategy.

## Outstanding Questions

### Resolve Before Planning

*(None — all product decisions are captured above.)*

### Deferred to Planning

- [Affects R1][Needs research] What specific wait strategies does Playwright support natively (e.g., `wait_for_load_state`, `wait_for_selector`) and which need custom implementation?
- [Affects R2][Needs research] What readability/extraction libraries exist in Python that could augment the current BeautifulSoup + markdownify pipeline?
- [Affects R5][Technical] How should the `asyncio.Lock` serialization change for multi-tab? Per-tab locks? Reader-writer lock? Or keep global serialization?
- [Affects R6][Technical] How does Playwright handle downloads through a SOCKS5 proxy? Does `context.on("download")` fire correctly?
- [Affects R9][Technical] What schema format should structured extraction accept? JSON Schema? A simpler custom format? How should missing fields be handled?
- [Affects R10][Needs research] What pagination detection heuristics exist? Should this use link-rel="next", URL pattern detection, or element-based detection?
- [Affects R11][Needs research] What page classification approach is most reliable — heuristic HTML analysis, or leveraging the AI client's own classification via tool response metadata?

## Next Steps

`-> /ce:plan` for structured implementation planning (recommend starting with Phase 1 only to keep planning scope manageable)

# Configuration Revision and Provider‑Specific Settings

Dirracuda’s current configuration panel includes Shodan‑specific fields (API key, default query, region/country filters).  With the promotion of additional search providers, configuration needs to be decentralised to avoid clutter and to respect the locality of settings.  This document proposes how to revise configuration management.

## 1. Remove global Shodan fields

* **API key storage:** The Shodan API key should no longer be configured in a global config panel.  Instead, move this field into the Shodan provider options panel within the new start‑scan dialog.  The key can still be stored in the same location on disk (e.g., `~/.dirracuda/shodan_api_key`), but the UI for editing it should live alongside other Shodan options.
* **Default query and filters:** Any global defaults for Shodan (e.g., default query string, country filter) should likewise be removed from the main config.  Users will set these defaults in the Shodan panel or rely on templates.  This keeps provider‑specific logic away from the global namespace.

## 2. Introduce per‑provider configuration objects

* **ProviderOptions vs. Config:** Distinguish between transient scan options (selected in the start‑scan dialog) and persistent configuration (stored between sessions).  Each provider should define a `ProviderConfig` dataclass with fields such as API keys, favourite queries, and other persistent preferences.
* **Storage locations:** Save each provider’s configuration to its own file or section within a unified config file (e.g., `~/.dirracuda/config.yml`).  Namespacing prevents collisions (e.g., `providers.shodan.api_key`, `providers.searxng.instance_url`).
* **Management UI:** Provide a separate configuration editor accessible from the accessories menu or a “Settings” button.  This editor lists all providers with their persistent settings and allows users to edit API keys, set default result limits, or clear cached data.  Use the normalised UI template to ensure consistency.

## 3. Deprecate unused config entries

* **Audit existing config keys:** Identify configuration keys that are no longer used (e.g., experimental flags for SearXNG or Reddit) and mark them for deprecation.  Provide a migration script that deletes old keys or migrates them to the new provider‑specific structure.
* **Versioning:** Add a config version number to detect outdated configs.  On startup, check the version and apply migrations automatically if needed.

## 4. Security considerations

* **Sensitive data handling:** API keys and secrets should be stored securely.  Where possible, use OS‑level credential storage (e.g., keychain, credential helper) or encrypt the configuration file.  Provide a warning that storing credentials in plaintext may pose a risk.
* **Environment variables:** Allow API keys to be supplied via environment variables for automated deployments or headless use, overriding values stored in the config file.

## 5. Impact on code

* Update the `gui/config_dialog.py` and related modules to remove Shodan‑specific controls.  Replace them with a simple message directing users to the provider options panel.
* Modify provider code to read from the new `ProviderConfig` objects rather than global config keys.  Each provider should expose `load_config()` and `save_config()` methods.
* Provide a “Configure provider” button next to each provider in the start‑scan dialog that opens the provider’s configuration editor.

By decentralising configuration to provider‑specific panels and removing Shodan‑centric entries from the global config, Dirracuda will offer a cleaner settings interface and reduce confusion when multiple search providers are available.
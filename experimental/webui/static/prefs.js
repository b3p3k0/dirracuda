(function () {
  "use strict";

  var CONSENT_KEY = "dirracuda_pref_consent_v1";
  var DATA_KEY = "dirracuda_pref_data_v1";

  var ALLOWED_PROTOCOLS = {all: true, smb: true, ftp: true, http: true};

  function _storage() {
    try {
      if (!window.localStorage) {
        return null;
      }
      var probe = "__dirracuda_pref_probe__";
      window.localStorage.setItem(probe, "1");
      window.localStorage.removeItem(probe);
      return window.localStorage;
    } catch (_) {
      return null;
    }
  }

  function _asBool(value, fallback) {
    if (typeof value === "boolean") {
      return value;
    }
    return fallback;
  }

  function _asIntInRange(value, min, max, fallback) {
    if (typeof value === "number" && Number.isInteger(value) && value >= min && value <= max) {
      return value;
    }
    return fallback;
  }

  function _cleanResults(section) {
    var src = section && typeof section === "object" ? section : {};
    var protocol = typeof src.protocol === "string" ? src.protocol.toLowerCase() : "all";
    if (!ALLOWED_PROTOCOLS[protocol]) {
      protocol = "all";
    }
    return {
      protocol: protocol,
      shares_only: _asBool(src.shares_only, false),
      favorites_only: _asBool(src.favorites_only, false),
      hide_avoid: _asBool(src.hide_avoid, false)
    };
  }

  function _cleanScans(section) {
    var src = section && typeof section === "object" ? section : {};
    return {
      proto_smb: _asBool(src.proto_smb, true),
      proto_ftp: _asBool(src.proto_ftp, true),
      proto_http: _asBool(src.proto_http, true),
      max_results: _asIntInRange(src.max_results, 1, 100000, 100),
      probe: _asBool(src.probe, false),
      rescan_all: _asBool(src.rescan_all, false),
      rescan_failed: _asBool(src.rescan_failed, false)
    };
  }

  function _cleanData(data) {
    var src = data && typeof data === "object" ? data : {};
    return {
      results: _cleanResults(src.results),
      scans: _cleanScans(src.scans)
    };
  }

  function _readData() {
    var store = _storage();
    if (!store) {
      return _cleanData({});
    }
    try {
      var raw = store.getItem(DATA_KEY);
      if (!raw) {
        return _cleanData({});
      }
      var parsed = JSON.parse(raw);
      return _cleanData(parsed);
    } catch (_) {
      return _cleanData({});
    }
  }

  function _writeData(data) {
    var store = _storage();
    if (!store) {
      return false;
    }
    try {
      var cleaned = _cleanData(data);
      store.setItem(DATA_KEY, JSON.stringify(cleaned));
      return true;
    } catch (_) {
      return false;
    }
  }

  function getConsent() {
    var store = _storage();
    if (!store) {
      return null;
    }
    try {
      var value = store.getItem(CONSENT_KEY);
      if (value === "allow" || value === "deny") {
        return value;
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  function setConsent(value) {
    if (value !== "allow" && value !== "deny") {
      throw new Error("invalid consent value");
    }
    var store = _storage();
    if (!store) {
      return false;
    }
    try {
      store.setItem(CONSENT_KEY, value);
      return true;
    } catch (_) {
      return false;
    }
  }

  function readSection(section) {
    if (section !== "results" && section !== "scans") {
      return {};
    }
    var data = _readData();
    return data[section] || {};
  }

  function writeSection(section, values) {
    if (getConsent() !== "allow") {
      return false;
    }
    if (section !== "results" && section !== "scans") {
      return false;
    }
    var data = _readData();
    data[section] = section === "results" ? _cleanResults(values) : _cleanScans(values);
    return _writeData(data);
  }

  function clearData() {
    var store = _storage();
    if (!store) {
      return false;
    }
    try {
      store.removeItem(DATA_KEY);
      return true;
    } catch (_) {
      return false;
    }
  }

  function clearAll() {
    var store = _storage();
    if (!store) {
      return false;
    }
    try {
      store.removeItem(DATA_KEY);
      store.removeItem(CONSENT_KEY);
      return true;
    } catch (_) {
      return false;
    }
  }

  window.DirracudaPrefs = {
    CONSENT_KEY: CONSENT_KEY,
    DATA_KEY: DATA_KEY,
    isAvailable: function () {
      return !!_storage();
    },
    getConsent: getConsent,
    setConsent: setConsent,
    readSection: readSection,
    writeSection: writeSection,
    clearData: clearData,
    clearAll: clearAll
  };
})();

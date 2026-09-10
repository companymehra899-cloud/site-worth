import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)
CACHE = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 6 * 3600
CTX = ssl.create_default_context()
UA = "WorthCheck/1.0 (+https://localhost; research estimator)"

MULTI_TLD = {
    "co.uk", "com.au", "co.in", "com.br", "co.jp", "co.kr", "com.mx",
    "co.za", "com.tr", "com.ar", "ne.jp", "co.nz", "com.sg", "co.id"
}

COUNTRY_CPM = {
    "US": 6.4, "CA": 5.2, "GB": 5.8, "UK": 5.8, "AU": 5.4, "NZ": 4.6,
    "DE": 4.8, "FR": 4.4, "NL": 4.9, "CH": 7.2, "SE": 4.7, "NO": 6.1,
    "DK": 5.0, "FI": 4.3, "IE": 5.1, "SG": 4.8, "JP": 4.2, "KR": 3.6,
    "AE": 4.0, "IL": 4.5, "AT": 4.1, "BE": 4.0, "IT": 2.8, "ES": 2.6,
    "IN": 1.15, "ID": 0.85, "PK": 0.7, "BD": 0.65, "PH": 0.9, "VN": 0.95,
    "NG": 0.8, "EG": 0.75, "KE": 0.7, "ZA": 1.6, "BR": 1.7, "MX": 1.5,
    "AR": 1.2, "TR": 1.4, "TH": 1.3, "MY": 1.6, "TW": 2.4, "HK": 3.8,
    "CN": 1.8, "RU": 1.1, "UA": 0.9, "PL": 2.0, "RO": 1.4, "CZ": 2.1
}

HIGH_CPM_WORDS = (
    "bank", "loan", "insur", "mortgage", "credit", "crypto", "forex",
    "casino", "betting", "lawyer", "attorney", "hosting", "cloud", "saas"
)
LOW_CPM_WORDS = ("wiki", "edu", "gov", "blog", "forum", "news", "tv", "video")

TOP_SITES = [
    "google.com", "youtube.com", "facebook.com", "instagram.com", "amazon.com",
    "wikipedia.org", "reddit.com", "yahoo.com", "twitter.com", "linkedin.com",
    "netflix.com", "microsoft.com", "github.com", "whatsapp.com", "bing.com",
    "indiatimes.com", "flipkart.com", "hotstar.com", "nytimes.com", "bbc.com"
]


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._grab = False
        self.title = ""
        self.desc = ""

    def handle_starttag(self, tag, attrs):
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._grab = True
        if tag == "meta":
            name = (ad.get("name") or ad.get("property") or "").lower()
            if name in ("description", "og:description") and not self.desc:
                self.desc = ad.get("content", "")[:220]

    def handle_endtag(self, tag):
        if tag == "title":
            self._grab = False

    def handle_data(self, data):
        if self._grab and not self.title:
            self.title = re.sub(r"\s+", " ", data).strip()[:140]


def http_get(url, headers=None, timeout=8, max_bytes=120000):
    h = {"User-Agent": UA, "Accept": "application/json,text/html,*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        data = resp.read(max_bytes)
        return resp.status, resp.getheader("Content-Type", ""), data


def http_json(url, headers=None, timeout=8):
    status, _, data = http_get(url, headers=headers, timeout=timeout)
    return status, json.loads(data.decode("utf-8", "replace"))


def clean_domain(raw):
    text = (raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0].split("?")[0].split("#")[0]
    text = text.split(":")[0]
    if text.startswith("www."):
        text = text[4:]
    text = text.strip(".")
    if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", text):
        return ""
    return text


def registrable(domain):
    parts = domain.split(".")
    if len(parts) < 2:
        return domain
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:]) if len(parts) >= 3 else last2
    if last2 in MULTI_TLD and len(parts) >= 3:
        return last3
    return last2


def cache_get(key):
    with CACHE_LOCK:
        item = CACHE.get(key)
        if not item:
            return None
        if time.time() - item[0] > CACHE_TTL:
            CACHE.pop(key, None)
            return None
        return item[1]


def cache_set(key, value):
    with CACHE_LOCK:
        CACHE[key] = (time.time(), value)


def fetch_tranco(domain):
    cached = cache_get("tranco:" + domain)
    if cached is not None:
        return cached
    try:
        _, payload = http_json("https://tranco-list.eu/api/ranks/domain/" + urllib.parse.quote(domain), timeout=10)
        ranks = payload.get("ranks") or []
        cache_set("tranco:" + domain, ranks)
        return ranks
    except Exception:
        cache_set("tranco:" + domain, [])
        return []


def fetch_rdap(domain):
    cached = cache_get("rdap:" + domain)
    if cached is not None:
        return cached
    info = {"registrar": None, "created": None, "expires": None, "status": [], "nameservers": []}
    try:
        _, data = http_json("https://rdap.org/domain/" + urllib.parse.quote(domain), timeout=10)
        for ev in data.get("events") or []:
            action = (ev.get("eventAction") or "").lower()
            date = (ev.get("eventDate") or "")[:10]
            if action in ("registration", "registered") and not info["created"]:
                info["created"] = date
            if action in ("expiration", "expired") and not info["expires"]:
                info["expires"] = date
        for ent in data.get("entities") or []:
            roles = [str(r).lower() for r in (ent.get("roles") or [])]
            if "registrar" in roles:
                vcard = ent.get("vcardArray") or []
                if isinstance(vcard, list) and len(vcard) > 1:
                    for row in vcard[1]:
                        if row and row[0] == "fn":
                            info["registrar"] = row[-1]
                if not info["registrar"]:
                    info["registrar"] = ent.get("handle")
        info["status"] = list(data.get("status") or [])[:6]
        info["nameservers"] = [
            (ns.get("ldhName") or ns.get("unicodeName") or "").lower()
            for ns in (data.get("nameservers") or [])
        ][:6]
    except Exception:
        pass
    cache_set("rdap:" + domain, info)
    return info


def fetch_geo(host):
    cached = cache_get("geo:" + host)
    if cached is not None:
        return cached
    info = {
        "ip": None, "country": None, "countryCode": None, "city": None,
        "isp": None, "org": None, "as": None
    }
    try:
        url = "http://ip-api.com/json/" + urllib.parse.quote(host) + "?fields=status,country,countryCode,city,isp,org,query,as"
        _, data = http_json(url, timeout=8)
        if data.get("status") == "success":
            info.update({
                "ip": data.get("query"),
                "country": data.get("country"),
                "countryCode": data.get("countryCode"),
                "city": data.get("city"),
                "isp": data.get("isp"),
                "org": data.get("org"),
                "as": data.get("as")
            })
    except Exception:
        pass
    cache_set("geo:" + host, info)
    return info


def fetch_dns(domain):
    cached = cache_get("dns:" + domain)
    if cached is not None:
        return cached
    records = []
    try:
        url = "https://cloudflare-dns.com/dns-query?name=" + urllib.parse.quote(domain) + "&type=A"
        _, data = http_json(url, headers={"Accept": "application/dns-json"}, timeout=8)
        for ans in data.get("Answer") or []:
            if ans.get("type") == 1:
                records.append({"ip": ans.get("data"), "ttl": ans.get("TTL")})
    except Exception:
        try:
            ip = socket.gethostbyname(domain)
            records.append({"ip": ip, "ttl": None})
        except Exception:
            pass
    cache_set("dns:" + domain, records)
    return records


def fetch_site_meta(domain):
    cached = cache_get("meta:" + domain)
    if cached is not None:
        return cached
    meta = {"title": None, "description": None, "reachable": False, "https": False, "status": None}
    for scheme in ("https", "http"):
        try:
            status, ctype, data = http_get(scheme + "://" + domain, timeout=7, max_bytes=20000)
            meta["status"] = status
            meta["reachable"] = status < 400
            meta["https"] = scheme == "https"
            if "html" in (ctype or "").lower() or data[:200].lower().find(b"<html") != -1 or data.lower().find(b"<title") != -1:
                parser = TitleParser()
                try:
                    parser.feed(data.decode("utf-8", "replace"))
                except Exception:
                    pass
                meta["title"] = parser.title or None
                meta["description"] = parser.desc or None
            break
        except Exception:
            continue
    cache_set("meta:" + domain, meta)
    return meta


def domain_age_years(created):
    if not created:
        return None
    try:
        dt = datetime.strptime(created[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).days
        return round(max(days, 0) / 365.25, 1)
    except Exception:
        return None


def hash_rank(domain):
    h = 2166136261
    for ch in domain.encode():
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return 1200000 + (h % 3800000)


def estimate_from_rank(rank, domain, country_code, created):
    daily_visitors = 900000000.0 / max(rank, 1)
    if rank > 1000000:
        daily_visitors *= 0.55
    pages = 3.15
    if rank <= 50:
        pages = 3.6
    elif rank <= 500:
        pages = 3.25
    elif rank <= 5000:
        pages = 2.95
    elif rank <= 50000:
        pages = 2.6
    else:
        pages = 2.25
    cpm = COUNTRY_CPM.get((country_code or "").upper(), 2.2)
    name = domain.lower()
    if any(w in name for w in HIGH_CPM_WORDS):
        cpm *= 1.55
    elif any(w in name for w in LOW_CPM_WORDS):
        cpm *= 0.72
    age = domain_age_years(created)
    if age is not None:
        if age >= 15:
            cpm *= 1.08
        elif age < 2:
            daily_visitors *= 0.85
            cpm *= 0.9
    daily_pv = daily_visitors * pages
    daily_rev = (daily_pv / 1000.0) * cpm
    return {
        "dailyVisitors": daily_visitors,
        "dailyPageviews": daily_pv,
        "pagesPerVisit": pages,
        "cpm": cpm,
        "dailyRevenue": daily_rev,
        "monthlyVisitors": daily_visitors * 30,
        "monthlyPageviews": daily_pv * 30,
        "monthlyRevenue": daily_rev * 30,
        "yearlyVisitors": daily_visitors * 365,
        "yearlyPageviews": daily_pv * 365,
        "yearlyRevenue": daily_rev * 365,
        "worth": daily_rev * 365 * 6
    }


def analyze_domain(raw):
    domain = clean_domain(raw)
    if not domain:
        return {"error": "Invalid domain. Example: wikipedia.org"}
    root = registrable(domain)

    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_tranco = pool.submit(fetch_tranco, domain)
        fut_tranco_root = pool.submit(fetch_tranco, root) if root != domain else None
        fut_rdap = pool.submit(fetch_rdap, root)
        fut_geo = pool.submit(fetch_geo, domain)
        fut_dns = pool.submit(fetch_dns, domain)
        fut_meta = pool.submit(fetch_site_meta, domain)
        ranks = fut_tranco.result()
        if not ranks and fut_tranco_root:
            ranks = fut_tranco_root.result()
            if ranks:
                domain = root
        rdap = fut_rdap.result()
        geo = fut_geo.result()
        dns = fut_dns.result()
        meta = fut_meta.result()

    ranked = bool(ranks)
    current_rank = ranks[0]["rank"] if ranks else hash_rank(domain)
    history = ranks[:30]
    history = list(reversed(history))
    if len(history) >= 2:
        old = history[0]["rank"]
        new = history[-1]["rank"]
        change = old - new
    else:
        change = 0
    est = estimate_from_rank(current_rank, domain, geo.get("countryCode"), rdap.get("created"))
    low = dict(est)
    high = dict(est)
    for key in ("dailyRevenue", "monthlyRevenue", "yearlyRevenue", "worth"):
        low[key] = est[key] * 0.55
        high[key] = est[key] * 1.7
    return {
        "domain": domain,
        "root": root,
        "favicon": "https://www.google.com/s2/favicons?sz=64&domain=" + domain,
        "meta": meta,
        "rank": {
            "value": current_rank,
            "source": "Tranco" if ranked else "Modelled (not in Tranco top list)",
            "ranked": ranked,
            "change30d": change,
            "history": history
        },
        "geo": geo,
        "dns": dns,
        "rdap": rdap,
        "ageYears": domain_age_years(rdap.get("created")),
        "estimate": est,
        "range": {"low": low, "high": high},
        "method": {
            "traffic": "Daily visitors are modelled from live Tranco global rank using a power curve calibrated to large public sites.",
            "revenue": "Ad revenue uses country CPM x estimated pageviews. This is an estimate, not actual AdSense data.",
            "worth": "Sale value is estimated at 6x yearly ad revenue, similar to public website-worth tools."
        },
        "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    }


def nocache(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/")
def home():
    return nocache(send_from_directory(".", "index.html"))


@app.route("/wc.css")
def wc_css():
    return nocache(send_from_directory(".", "wc.css"))


@app.route("/styles.css")
def css():
    return nocache(send_from_directory(".", "wc.css"))


@app.route("/app.js")
def js():
    return nocache(send_from_directory(".", "app.js"))


@app.route("/api/analyze")
def api_analyze():
    domain = request.args.get("domain", "")
    result = analyze_domain(domain)
    code = 400 if result.get("error") else 200
    return jsonify(result), code


@app.route("/api/topsites")
def api_tops():
    cached = cache_get("topsites")
    if cached:
        return jsonify(cached)
    rows = []
    for domain in TOP_SITES:
        ranks = fetch_tranco(domain)
        rank = ranks[0]["rank"] if ranks else hash_rank(domain)
        est = estimate_from_rank(rank, domain, "US", None)
        rows.append({
            "domain": domain,
            "rank": rank,
            "worth": est["worth"],
            "monthlyRevenue": est["monthlyRevenue"],
            "dailyVisitors": est["dailyVisitors"],
            "favicon": "https://www.google.com/s2/favicons?sz=32&domain=" + domain
        })
        time.sleep(0.15)
    rows.sort(key=lambda x: x["rank"])
    cache_set("topsites", rows)
    return jsonify(rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)

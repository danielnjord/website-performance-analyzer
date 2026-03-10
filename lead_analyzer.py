import os
import re
import pandas as pd
import requests
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = "AIzaSyAmAiMBYXrH2h5GLKifsgagsECCiu6wvuU"

PERFORMANCE_THRESHOLD = 70
LCP_THRESHOLD_MS = 3500
MAX_WORKERS = 6

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def normalize_site(site):
    site = str(site).strip()

    if not site:
        return None

    if not site.startswith("http://") and not site.startswith("https://"):
        site = "https://" + site

    parsed = urlparse(site)

    if not parsed.netloc:
        return None

    return f"{parsed.scheme}://{parsed.netloc}"


def is_blocked_website(url):
    if not url:
        return True

    blocked_domains = {
        "google.com",
        "www.google.com",
        "maps.google.com",
        "facebook.com",
        "www.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "linkedin.com",
        "www.linkedin.com",
        "youtube.com",
        "www.youtube.com",
        "tiktok.com",
        "www.tiktok.com",
        "eniro.se",
        "www.eniro.se",
        "hitta.se",
        "www.hitta.se",
        "reco.se",
        "www.reco.se",
        "bokadirekt.se",
        "www.bokadirekt.se",
        "offerta.se",
        "www.offerta.se",
        "allabolag.se",
        "www.allabolag.se",
    }

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    return any(blocked in domain for blocked in blocked_domains)


def extract_domain_candidates_from_text(text):
    if not text:
        return []

    pattern = r'https?://[^\s"\'<>]+'
    urls = re.findall(pattern, text)

    candidates = []

    for url in urls:
        cleaned = normalize_site(url)
        if not cleaned:
            continue

        if is_blocked_website(cleaned):
            continue

        candidates.append(cleaned)

    return list(dict.fromkeys(candidates))


def resolve_website_from_maps_url(maps_url):
    if not maps_url or pd.isna(maps_url):
        return None

    try:
        r = requests.get(str(maps_url), timeout=12, headers=HEADERS, allow_redirects=True)
        html = r.text

        candidates = extract_domain_candidates_from_text(html)

        if candidates:
            return candidates[0]

    except Exception:
        pass

    return None


def pick_best_website(row):
    website = row.get("website")
    maps_url = row.get("url")

    if pd.notna(website) and str(website).strip():
        normalized = normalize_site(website)
        if normalized and not is_blocked_website(normalized):
            return normalized

    if pd.notna(maps_url) and str(maps_url).strip():
        resolved = resolve_website_from_maps_url(maps_url)
        if resolved and not is_blocked_website(resolved):
            return resolved

    return None


def is_wordpress(site):
    try:
        r = requests.get(site, timeout=6, headers=HEADERS, allow_redirects=True)
        html = r.text.lower()

        return (
            "wp-content" in html
            or "wp-includes" in html
            or "wp-json" in html
        )
    except Exception:
        return False


def get_pagespeed_data(site):
    if not API_KEY:
        return {
            "performance": None,
            "seo": None,
            "lcp": None,
        }

    try:
        url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        params = {
            "url": site,
            "key": API_KEY,
            "strategy": "mobile",
            "category": ["performance", "seo"],
        }

        r = requests.get(url, params=params, timeout=20)
        data = r.json()

        lighthouse = data.get("lighthouseResult", {})
        categories = lighthouse.get("categories", {})
        audits = lighthouse.get("audits", {})

        performance = categories.get("performance", {}).get("score")
        seo = categories.get("seo", {}).get("score")
        lcp = audits.get("largest-contentful-paint", {}).get("numericValue")

        return {
            "performance": int(performance * 100) if performance is not None else None,
            "seo": int(seo * 100) if seo is not None else None,
            "lcp": int(lcp) if lcp is not None else None,
        }

    except Exception:
        return {
            "performance": None,
            "seo": None,
            "lcp": None,
        }


def calculate_lead_score(performance, lcp, seo):
    score = 0

    if lcp is not None:
        if lcp > 6000:
            score += 45
        elif lcp > 5000:
            score += 35
        elif lcp > 3500:
            score += 20

    if performance is not None:
        if performance < 40:
            score += 35
        elif performance < 55:
            score += 25
        elif performance < 70:
            score += 10

    if seo is not None:
        if seo < 60:
            score += 15
        elif seo < 75:
            score += 8

    return score


def deobfuscate_text(text):
    if not text:
        return ""

    text = str(text)

    replacements = [
        (r"\[at\]", "@"),
        (r"\(at\)", "@"),
        (r"\sat\s", "@"),
        (r"\[dot\]", "."),
        (r"\(dot\)", "."),
        (r"\sdot\s", "."),
    ]

    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    return text


def extract_emails(text):
    if not text:
        return []

    text = deobfuscate_text(text)

    pattern = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    emails = re.findall(pattern, text)

    blocked_domains = {
        "example.com",
        "domain.com",
        "email.com",
        "wix.com",
    }

    cleaned = []
    for email in emails:
        email = email.strip().lower().rstrip(".,;:!?)]}\"'")
        domain = email.split("@")[-1]

        if domain in blocked_domains:
            continue

        if email.startswith("noreply@") or email.startswith("no-reply@"):
            continue

        if any(img in email for img in [".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"]):
            continue

        cleaned.append(email)

    return sorted(set(cleaned))


def fetch_html(url):
    try:
        r = requests.get(url, timeout=8, headers=HEADERS, allow_redirects=True)
        if r.ok:
            return r.text, r.url
    except Exception:
        pass
    return "", None


def same_domain(site, final_url):
    if not site or not final_url:
        return False

    try:
        d1 = urlparse(site).netloc.lower().replace("www.", "")
        d2 = urlparse(final_url).netloc.lower().replace("www.", "")
        return d1 == d2
    except Exception:
        return False


def prioritize_emails(emails):
    if not emails:
        return None

    priority = ["info@", "kontakt@", "hello@", "hej@", "mail@"]
    unique_emails = sorted(set(emails))

    for prefix in priority:
        for email in unique_emails:
            if email.startswith(prefix):
                return email

    return unique_emails[0]


def find_email(site):
    urls = [
        site,
        urljoin(site + "/", "kontakt"),
        urljoin(site + "/", "kontakt/"),
        urljoin(site + "/", "kontakt-oss"),
        urljoin(site + "/", "kontakt-oss/"),
        urljoin(site + "/", "contact"),
        urljoin(site + "/", "contact/"),
        urljoin(site + "/", "om-oss"),
        urljoin(site + "/", "om-oss/"),
        urljoin(site + "/", "about"),
        urljoin(site + "/", "about/"),
        urljoin(site + "/", "om"),
        urljoin(site + "/", "om/"),
        urljoin(site + "/", "team"),
        urljoin(site + "/", "team/"),
        urljoin(site + "/", "kundservice"),
        urljoin(site + "/", "kundservice/"),
        urljoin(site + "/", "integritet"),
        urljoin(site + "/", "integritet/"),
        urljoin(site + "/", "privacy"),
        urljoin(site + "/", "privacy/"),
    ]

    emails = []

    for url in dict.fromkeys(urls):
        html, final_url = fetch_html(url)

        if not html:
            continue

        if final_url and not same_domain(site, final_url):
            continue

        found = extract_emails(html)
        if found:
            emails.extend(found)

        mailto_matches = re.findall(r"mailto:([^\"'>\s?]+)", html, flags=re.IGNORECASE)
        for mailto in mailto_matches:
            emails.extend(extract_emails(mailto))

    return prioritize_emails(emails)


def analyze_lighthouse_row(row):
    company = row["title"]
    site = row["website_clean"]

    psi = get_pagespeed_data(site)
    performance = psi["performance"]
    seo = psi["seo"]
    lcp = psi["lcp"]

    bad = (
        (performance is not None and performance < PERFORMANCE_THRESHOLD)
        or (lcp is not None and lcp > LCP_THRESHOLD_MS)
    )

    if not bad:
        return None

    return {
        "company": company,
        "website": site,
        "performance_mobile": performance,
        "lcp_seconds": round(lcp / 1000, 1) if lcp is not None else None,
        "seo": seo,
        "lead_score": calculate_lead_score(performance, lcp, seo),
    }


csv_files = [
    f for f in os.listdir()
    if f.endswith(".csv") and not f.startswith("qualified")
]

print("\nCSV-filer hittade:")
for f in csv_files:
    print(" -", f)

for input_file in csv_files:
    print("\n===============================")
    print("Analyserar:", input_file)
    print("===============================\n")

    df = pd.read_csv(input_file)

    if "title" not in df.columns:
        print(f"Saknar 'title' i {input_file}, hoppar över.")
        continue

    if "website" not in df.columns and "url" not in df.columns:
        print(f"Saknar både 'website' och 'url' i {input_file}, hoppar över.")
        continue

    total_rows_before = len(df)
    print(f"Antal rader i filen: {total_rows_before}")

    print("\nSteg 0: Hämtar hemsidor från website/url\n")

    resolved_sites = []
    total_rows = len(df)

    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        company = row["title"]
        site = pick_best_website(row)

        print(f"[SITE {idx}/{total_rows}] {company} -> {site}")

        resolved_sites.append(site)

    df["website_clean"] = resolved_sites
    df = df[df["website_clean"].notna()].copy()
    df = df.drop_duplicates(subset=["website_clean"])

    print(f"\nRader med användbar hemsida: {len(df)}\n")

    print("Steg 1: WordPress detection\n")

    wordpress_rows = []
    total_sites = len(df)

    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        company = row["title"]
        site = row["website_clean"]

        print(f"[WP {idx}/{total_sites}] {company} | {site}")

        if is_wordpress(site):
            wordpress_rows.append(row)
            print("  -> WordPress")

    wp_total = len(wordpress_rows)
    print(f"\nWordPress-sidor hittade: {wp_total}\n")

    print("Steg 2: Lighthouse analys\n")

    qualified = []
    lead_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(analyze_lighthouse_row, row) for row in wordpress_rows]

        for lighthouse_idx, future in enumerate(as_completed(futures), start=1):
            result = future.result()

            print(f"[LIGHTHOUSE {lighthouse_idx}/{wp_total}] klar")

            if result:
                lead_count += 1
                print(
                    f"  -> [LEAD {lead_count}] {result['company']} | "
                    f"Perf {result['performance_mobile']} | "
                    f"LCP {result['lcp_seconds']}s | "
                    f"SEO {result['seo']} | "
                    f"Score {result['lead_score']}"
                )
                qualified.append(result)

    print(f"\nKvalificerade leads: {len(qualified)}\n")

    print("Steg 3: Email scraping\n")

    qualified_total = len(qualified)
    qualified_with_email = []

    for email_idx, lead in enumerate(qualified, start=1):
        email = find_email(lead["website"])
        print(f"[EMAIL {email_idx}/{qualified_total}] {lead['company']} -> {email}")

        if email:
            lead["email"] = email
            qualified_with_email.append(lead)

    base_name = os.path.splitext(input_file)[0]
    output_file = f"qualified_leads_with_email_{base_name}.csv"

    df_out = pd.DataFrame(qualified_with_email)

    if not df_out.empty:
        df_out = df_out[
            [
                "company",
                "website",
                "email",
                "performance_mobile",
                "lcp_seconds",
                "seo",
                "lead_score",
            ]
        ]
        df_out = df_out.sort_values(
            by=["lead_score", "lcp_seconds"],
            ascending=[False, False]
        )

    df_out.to_csv(output_file, index=False, encoding="utf-8-sig")

    print(f"\nFil skapad: {output_file}")
    print(f"Leads med email sparade: {len(df_out)}")
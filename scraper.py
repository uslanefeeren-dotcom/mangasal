"""
Mangasal Scraper v5 — ÇALIŞAN SON SÜRÜM
"""
import sqlite3, json, time, re, logging, os
from datetime import datetime
from threading import Thread
from functools import wraps
import hashlib, secrets

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, Response, stream_with_context, send_from_directory
from flask_cors import CORS

# ─── AYARLAR ───────────────────────────────────
SOURCES = {
    "golgebahcesi": {
        "base":     "https://golgebahcesi.com",
        "popular":  "https://golgebahcesi.com/manga/?order=popular",
        "latest":   "https://golgebahcesi.com/manga/?order=latest",
        "parser":   "bsx",
        "page_url": None,
    },
    "ragnarscans": {
        "base":     "https://ragnarscans.com",
        "popular":  "https://ragnarscans.com/manga/",
        "latest":   "https://ragnarscans.com/manga/",
        "parser":   "ragnar",
        "page_url": "https://ragnarscans.com/manga/page/{page}/",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# DB yolu mutlak yapıldı (Railway'de çalışma dizini sorununu önler)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voidscans.db")
INTERVAL = 2
DELAY    = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("Mangasal")

# ─── TÜR NORMALİZASYON ─────────────────────────
GENRE_MAP = {
    "aksiyon":"Aksiyon","action":"Aksiyon",
    "fantastik":"Fantazi","fantasy":"Fantazi","fantazi":"Fantazi","fantezi":"Fantazi","büyü":"Fantazi",
    "macera":"Macera","adventure":"Macera",
    "romance":"Romance","romantik":"Romance","romantizm":"Romance",
    "korku":"Korku","horror":"Korku",
    "gerilim":"Gerilim","thriller":"Gerilim","psikolojik":"Gerilim",
    "komedi":"Komedi","comedy":"Komedi",
    "dram":"Drama","drama":"Drama",
    "dövüş sanatları":"Dövüş Sanatları","martial arts":"Dövüş Sanatları","dövüş":"Dövüş Sanatları","murim":"Dövüş Sanatları",
    "bilim kurgu":"Sci-Fi","bilimkurgu":"Sci-Fi",
    "doğaüstü":"Doğaüstü","supernatural":"Doğaüstü",
    "isekai":"Isekai","yeniden doğuş":"Isekai","reenkarnasyon":"Isekai","ikinci şans":"Isekai",
    "oyun":"Oyun","sanal gerçeklik":"Oyun","sistem":"Oyun",
    "yaşamdan kesitler":"Slice of Life","slice of life":"Slice of Life","okul hayatı":"Slice of Life",
    "mature":"Mature","olgun":"Mature","seinen":"Mature",
    "gizem":"Gizem","dedektif":"Gizem",
    "canavar":"Canavar","aşırı güçlü":"Aşırı Güçlü","dahi mc":"Aşırı Güçlü",
    "gerileme":"Gerileme","kıyamet":"Kıyamet","intikam":"İntikam",
    "tarihsel":"Tarihsel","trajedi":"Trajedi","harem":"Harem",
}

SKIP = {"renk","manga","manhwa","manhua","webtoon","novel","comic",
        "makine çeviri","arşiv","shounen","genç","adult","hentai",
        "yetişkin","pompa123","enesbatur","completed","ongoing"}

def norm_genres(raw):
    seen, out = set(), []
    for g in raw:
        k = g.strip().lower()
        if not k or k in SKIP: continue
        label = GENRE_MAP.get(k, g.strip().title())
        if label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out[:6]

# ─── VERİTABANI ────────────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS series (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, slug TEXT, url TEXT UNIQUE,
        title TEXT, cover_url TEXT, description TEXT,
        status TEXT DEFAULT 'Devam Ediyor',
        content_type TEXT DEFAULT 'Manhwa',
        genres TEXT DEFAULT '[]',
        rating REAL DEFAULT 0,
        view_count INTEGER DEFAULT 0,
        chapter_count INTEGER DEFAULT 0,
        latest_chapter TEXT, latest_chapter_url TEXT,
        last_updated TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        series_id INTEGER,
        series_url TEXT,
        chapter_num TEXT,
        chapter_title TEXT,
        url TEXT UNIQUE,
        release_date TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS scrape_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, status TEXT, series_count INTEGER,
        message TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        username TEXT UNIQUE NOT NULL,
        role TEXT DEFAULT 'Yeni',
        roles_earned TEXT DEFAULT '["Yeni"]',
        custom_role TEXT DEFAULT '',
        avatar_url TEXT DEFAULT '',
        bio TEXT DEFAULT '',
        completed_count INTEGER DEFAULT 0,
        reading_count INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        last_login TEXT
    );
    CREATE TABLE IF NOT EXISTS user_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        series_url TEXT NOT NULL,
        series_title TEXT,
        series_cover TEXT,
        last_chapter_url TEXT,
        last_chapter_num TEXT,
        status TEXT DEFAULT 'reading',
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, series_url)
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT
    );
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id),
        series_url TEXT NOT NULL,
        series_title TEXT,
        series_cover TEXT,
        series_type TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, series_url)
    );
    CREATE TABLE IF NOT EXISTS site_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_views    ON series(view_count DESC);
    CREATE INDEX IF NOT EXISTS idx_updated  ON series(last_updated DESC);
    CREATE INDEX IF NOT EXISTS idx_type     ON series(content_type);
    CREATE INDEX IF NOT EXISTS idx_chap_ser ON chapters(series_url);
    CREATE INDEX IF NOT EXISTS idx_progress ON user_progress(user_id);
    CREATE INDEX IF NOT EXISTS idx_favs     ON favorites(user_id);
    """)
    defaults = [
        ('site_name', 'Mangasal'),
        ('site_name_part1', 'MANGA'),
        ('site_name_part2', 'SAL'),
        ('name_color', '#d4500a'),
        ('site_logo', ''),
        ('hero_banners', '[]'),
        ('hero_title', 'Okumayı'),
        ('hero_subtitle', 'Seç.'),
        ('hero_desc', 'Gölge Bahçesi ve Ragnar Scans serilerini tek platformda.'),
        ('primary_color', '#d4500a'),
        ('bg_color', '#0d0805'),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO site_settings(key,value) VALUES(?,?)", (k,v))
    c.commit(); c.close()
    log.info("Veritabanı hazır: %s", DB)

# ─── VERİTABANI FONKSİYONLARI ──────────────────
def rows(sql, params=()):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        if "genres" in d:
            try: d["genres"] = json.loads(d["genres"])
            except: d["genres"] = []
        out.append(d)
    c.close()
    return out

def save_series(data):
    c = sqlite3.connect(DB)
    c.execute("""
        INSERT INTO series (
            source,slug,url,title,cover_url,description,
            status,content_type,genres,rating,view_count,
            chapter_count,latest_chapter,latest_chapter_url,
            last_updated,updated_at
        ) VALUES (
            :source,:slug,:url,:title,:cover_url,:description,
            :status,:content_type,:genres,:rating,:view_count,
            :chapter_count,:latest_chapter,:latest_chapter_url,
            :last_updated,datetime('now')
        ) ON CONFLICT(url) DO UPDATE SET
            title=excluded.title, cover_url=excluded.cover_url,
            status=excluded.status, content_type=excluded.content_type,
            genres=excluded.genres, rating=excluded.rating,
            view_count=excluded.view_count, chapter_count=excluded.chapter_count,
            latest_chapter=excluded.latest_chapter,
            latest_chapter_url=excluded.latest_chapter_url,
            last_updated=excluded.last_updated,
            updated_at=datetime('now')
    """, data)
    c.commit(); c.close()

def save_chapters(series_url, chapters):
    c = sqlite3.connect(DB)
    for ch in chapters:
        try:
            c.execute("""
                INSERT OR IGNORE INTO chapters
                (series_url, chapter_num, chapter_title, url, release_date)
                VALUES (?, ?, ?, ?, ?)
            """, (series_url, ch["num"], ch["title"], ch["url"], ch["date"]))
        except Exception as e:
            log.debug("Bölüm kayıt hatası: %s", e)
    c.commit(); c.close()

# ─── SCRAPER ───────────────────────────────────
def get_page(url, session):
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning("Çekilemedi: %s → %s", url, e)
        return None

def parse_bsx(card, base_url=""):
    a = card.select_one("a")
    if not a: return None
    url   = a.get("href","")
    title = a.get("title","") or ""
    if not title:
        tt = a.select_one(".tt")
        title = tt.get_text(strip=True) if tt else ""
    if not title or not url: return None
    if url.startswith("../"):
        url = base_url + "/" + url.lstrip("../")
    elif url.startswith("/") and not url.startswith("//"):
        url = base_url + url
    elif not url.startswith("http"):
        url = base_url + "/" + url.lstrip("/")
    skip_words = ["duyuru", "announcement", "kapanış", "açılış", "haber",
                  "site ", "discord", "sponsor", "reklam", "bilgi"]
    if any(w in title.lower() for w in skip_words):
        return None
    img = a.select_one("img")
    cover = ""
    if img:
        cover = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
    if cover and cover.startswith("/") and not cover.startswith("//"):
        cover = base_url + cover
    type_span = a.select_one("span.type")
    content_type = "Manhwa"
    if type_span:
        for cls in (type_span.get("class") or []):
            if cls.lower() != "type":
                content_type = cls.strip().title()
                break
    ep_el = a.select_one(".epxs")
    latest = ep_el.get_text(strip=True) if ep_el else ""
    rating = 0.0
    sc = a.select_one(".numscore")
    if sc:
        try: rating = float(sc.get_text(strip=True).replace(",","."))
        except: pass
    return dict(url=url, slug=url.rstrip("/").split("/")[-1],
                title=title, cover=cover, content_type=content_type,
                latest_chapter=latest, rating=rating)

def parse_ragnar(card):
    a = card.select_one("a")
    if not a: return None
    url   = a.get("href","")
    if not url: return None
    title_el = card.select_one(".manga-overlay-title")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        title = a.get("title","") or ""
    if not title: return None
    img = card.select_one("img")
    cover = ""
    if img:
        cover = (img.get("data-src") or img.get("data-lazy-src") or img.get("src") or "")
    content_type = "Manhwa"
    genre_el = card.select_one(".manga-overlay-genres, .slider-genres")
    genres_raw = []
    if genre_el:
        genres_raw = [g.strip() for g in genre_el.get_text().split("·") if g.strip()]
    status_el = card.select_one(".manga-status-badge, .manga-status-ribbon")
    status = status_el.get_text(strip=True) if status_el else "Devam Ediyor"
    slug = "rg-" + url.rstrip("/").split("/")[-1]
    return dict(url=url, slug=slug, title=title, cover=cover,
                content_type=content_type, latest_chapter="",
                rating=0.0, genres_raw=genres_raw, status=status)

def scrape_list(source_key, url, session):
    parser_type = SOURCES[source_key].get("parser", "bsx")
    page_url_tpl = SOURCES[source_key].get("page_url", None)
    items, page = [], 1
    empty_streak = 0
    while page <= 100:
        if page_url_tpl and page > 1:
            purl = page_url_tpl.format(page=page)
        elif page_url_tpl and page == 1:
            purl = url
        else:
            sep  = "&" if "?" in url else "?"
            purl = f"{url}{sep}page={page}"
        r    = get_page(purl, session)
        if not r:
            empty_streak += 1
            if empty_streak >= 3: break
            page += 1
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        if parser_type == "ragnar":
            cards = soup.select(".manga-card")
        else:
            cards = soup.select(".bsx") or soup.select(".bs")
        if not cards:
            empty_streak += 1
            log.info("  %s sayfa %d: kart yok (%d/2)", source_key, page, empty_streak)
            if empty_streak >= 2: break
            page += 1
            time.sleep(DELAY)
            continue
        empty_streak = 0
        base_url = SOURCES[source_key]["base"]
        for card in cards:
            if parser_type == "ragnar":
                item = parse_ragnar(card)
            else:
                item = parse_bsx(card, base_url)
            if item: items.append(item)
        log.info("  %s sayfa %d → %d kart (toplam: %d)", source_key, page, len(cards), len(items))
        if parser_type == "ragnar":
            has_next = bool(soup.find("link", rel="next"))
            if not has_next: break
        else:
            next_btn = soup.select_one("a.next, .nextpostslink, a[rel='next'], a.next.page-numbers, .nav-previous a")
            if not next_btn: break
        page += 1
        time.sleep(DELAY)
    return items

def scrape_detail(url, session):
    import html as html_module
    r = get_page(url, session)
    if not r: return {}
    r.encoding = r.apparent_encoding or 'utf-8'
    soup = BeautifulSoup(r.text, "html.parser")
    desc_el = (soup.select_one(".entry-content") or
               soup.select_one(".synops") or
               soup.select_one(".summary__content") or
               soup.select_one(".description-summary"))
    desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""
    status = "Devam Ediyor"
    for el in soup.select(".tsinfo .imptdt, .infox .fmed, .imptdt, .post-status .summary-content"):
        t = el.get_text(strip=True).lower()
        if "completed" in t or "tamamlandı" in t: status="Tamamlandı"; break
        if "hiatus" in t or "durduruldu" in t:    status="Durduruldu"; break
    genre_els = (soup.select(".mgen a") or
                 soup.select(".genres-content a") or
                 soup.select("a[href*='/genres/']") or
                 soup.select("a[href*='/tur/']") or
                 soup.select("a[href*='/genre/']") or
                 soup.select(".manga-genres a") or
                 soup.select(".genre-list a"))
    raw_genres = [g.get_text(strip=True) for g in genre_els]
    if not raw_genres:
        genre_el = soup.select_one(".manga-genres, .genres, .series-genres")
        if genre_el:
            raw_genres = [g.strip() for g in genre_el.get_text().split("·") if g.strip()]
    chapters = []
    from urllib.parse import urljoin
    base_url = "/".join(url.split("/")[:3])
    def fix_url(u):
        if not u: return u
        if u.startswith("http"): return u
        return urljoin(url, u)
    ragnar_chaps = soup.select(".chapter-list a.uk-link-toggle, .chapter-list a")
    if ragnar_chaps:
        for a in ragnar_chaps:
            chap_url = a.get("href","")
            if not chap_url or "ragnarscans.com" not in chap_url: continue
            title_el = a.select_one("h3, h4, .uk-link-heading")
            num_text = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            num_text = num_text.strip()
            if not num_text: continue
            date_el = a.select_one("time, .uk-article-meta span:last-child")
            date = date_el.get("datetime","") or date_el.get_text(strip=True) if date_el else ""
            chapters.append({"num": num_text, "title": num_text, "url": chap_url, "date": date})
        log.info("Ragnar direkt: %d bölüm — %s", len(chapters), url.split("/")[-2])
    if not chapters:
        chapter_items = soup.select("#chapterlist li, .eplister li")
        for li in chapter_items:
            a = li.select_one("a")
            if not a: continue
            chap_url = fix_url(a.get("href",""))
            if not chap_url: continue
            num_el  = a.select_one(".chapternum")
            date_el = a.select_one(".chapterdate")
            num_text = html_module.unescape(num_el.get_text(strip=True) if num_el else li.get("data-num",""))
            date = date_el.get_text(strip=True) if date_el else ""
            chapters.append({"num": num_text, "title": num_text, "url": chap_url, "date": date})
    if not chapters:
        chapter_items = soup.select(".wp-manga-chapter, .listing-chapters_wrap li")
        for li in chapter_items:
            a = li.select_one("a")
            if not a: continue
            chap_url = fix_url(a.get("href",""))
            if not chap_url: continue
            num_text = a.get_text(strip=True)
            date_el = li.select_one(".chapter-release-date i, .chapter-release-date")
            date = date_el.get_text(strip=True) if date_el else ""
            chapters.append({"num": num_text, "title": num_text, "url": chap_url, "date": date})
    if not chapters:
        post_id = ""
        for pattern in [
            r'"postID"\s*:\s*"?(\d+)"?',
            r"var postID\s*=\s*'?(\d+)'?",
            r'data-id=["\'](\d+)["\']',
            r'data-post=["\'](\d+)["\']',
            r'"mangaID"\s*:\s*"?(\d+)"?',
        ]:
            m = re.search(pattern, r.text)
            if m: post_id = m.group(1); break
        if post_id:
            log.info("AJAX bölüm çekme: post_id=%s", post_id)
            try:
                base = url.split("/manga/")[0] if "/manga/" in url else \
                       url.split("/webtoon/")[0] if "/webtoon/" in url else \
                       "/".join(url.split("/")[:3])
                ajax_url = base + "/wp-admin/admin-ajax.php"
                ajax_r = session.post(
                    ajax_url,
                    data={"action":"manga_get_chapters","manga":post_id},
                    headers={**HEADERS,
                             "Referer": url,
                             "X-Requested-With": "XMLHttpRequest",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    timeout=15
                )
                if ajax_r.ok and len(ajax_r.text) > 10:
                    ajax_soup = BeautifulSoup(ajax_r.text, "html.parser")
                    for li in ajax_soup.select("li"):
                        a = li.select_one("a")
                        if not a: continue
                        chap_url = a.get("href","")
                        if not chap_url: continue
                        num_el  = a.select_one(".chapternum")
                        date_el = a.select_one(".chapterdate, .chapter-release-date i")
                        num_text = html_module.unescape(num_el.get_text(strip=True) if num_el else a.get_text(strip=True))
                        date = date_el.get_text(strip=True) if date_el else ""
                        chapters.append({"num": num_text, "title": num_text, "url": chap_url, "date": date})
                    log.info("AJAX: %d bölüm", len(chapters))
            except Exception as e:
                log.warning("AJAX hatası: %s", e)
    log.info("Toplam bölüm: %d — %s", len(chapters), url.split("/")[-2])
    return dict(description=desc, status=status, raw_genres=raw_genres,
                chapter_count=len(chapters), chapters=chapters,
                last_updated=chapters[0]["date"] if chapters else "")

def scrape_chapter_images(chap_url, session):
    r = get_page(chap_url, session)
    if not r: return []
    r.encoding = r.apparent_encoding or 'utf-8'
    html_text = r.text
    soup = BeautifulSoup(html_text, "html.parser")
    urls = []
    seen = set()
    if 'ts_reader.run' in html_text:
        try:
            start_idx = html_text.find('ts_reader.run(')
            if start_idx != -1:
                json_start = html_text.find('{', start_idx)
                depth = 0
                json_end = json_start
                for i in range(json_start, len(html_text)):
                    ch = html_text[i]
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            json_end = i + 1
                            break
                json_str = html_text[json_start:json_end]
                obj = json.loads(json_str)
                for source in obj.get('sources', []):
                    for img_url in source.get('images', []):
                        if img_url and img_url.startswith('http') and img_url not in seen:
                            seen.add(img_url)
                            urls.append(img_url)
                if urls:
                    log.info("ts_reader: %d resim — %s", len(urls), chap_url)
                    return urls
        except Exception as e:
            log.debug("ts_reader parse hatası: %s", e)
    selectors = [
        "#readerarea img", ".readerarea img", ".reading-content img",
        ".chapter-content img", ".page-break img", "div.chapter-c img",
        "div[class*='reader'] img", ".entry-content .separator img",
    ]
    imgs = []
    for sel in selectors:
        found = soup.select(sel)
        if found and len(found) > len(imgs):
            imgs = found
    if not imgs or len(imgs) < 2:
        main_content = soup.select_one('article, main, .content, #content, .post-body')
        if main_content:
            imgs = main_content.select('img')
        else:
            imgs = soup.select('img')
    for img in imgs:
        src = (img.get("data-src") or img.get("data-lazy-src") or
               img.get("data-original") or img.get("data-cfsrc") or
               img.get("data-pagespeed-lazy-src") or img.get("src") or "")
        if not src or not src.startswith("http"):
            continue
        if src in seen:
            continue
        skip_keywords = ['logo', 'icon', 'avatar', 'banner', 'ads', 'advertisement',
                         'loading', 'spinner', 'emoji', 'gravatar', 'wp-content/plugins',
                         'cover', 'poster', 'thumbnail', 'thumb', 'sidebar', 'widget',
                         'header', 'footer', 'nav', 'menu', 'button', 'social']
        if any(kw in src.lower() for kw in skip_keywords):
            continue
        width = img.get('width', '')
        height = img.get('height', '')
        try:
            w = int(str(width).replace('px','')) if width else 0
            h = int(str(height).replace('px','')) if height else 0
            if (w > 0 and w < 200) or (h > 0 and h < 200):
                continue
        except:
            pass
        img_class = ' '.join(img.get('class', []))
        if any(kw in img_class.lower() for kw in ['cover', 'thumb', 'avatar', 'logo']):
            continue
        seen.add(src)
        urls.append(src)
    log.info("Bolum resimleri (HTML): %s - %d resim", chap_url.split('/')[-2] if '/' in chap_url else 'unknown', len(urls))
    return urls

# ─── ANA SCRAPE ────────────────────────────────
def run(source_key, full=True):
    cfg  = SOURCES[source_key]
    sess = requests.Session()
    sess.headers.update(HEADERS)
    log.info("=== Scraping: %s (full=%s) ===", source_key, full)
    popular = scrape_list(source_key, cfg["popular"], sess)
    for i, x in enumerate(popular):
        x["est_views"] = max(100000 - i * 300, 100)
    time.sleep(DELAY * 2)
    latest = scrape_list(source_key, cfg["latest"], sess)
    all_items = {x["url"]: x for x in popular}
    for x in latest:
        if x["url"] not in all_items:
            x["est_views"] = 50
            all_items[x["url"]] = x
        else:
            if x.get("latest_chapter"):
                all_items[x["url"]]["latest_chapter"] = x["latest_chapter"]
    log.info("Toplam unique seri: %d", len(all_items))
    saved = 0
    for url, item in all_items.items():
        detail = {}
        if full:
            time.sleep(DELAY)
            detail = scrape_detail(url, sess)
            if detail.get("chapters"):
                save_chapters(url, detail["chapters"])
            if saved % 10 == 0:
                log.info("  Detay: %d/%d işlendi", saved, len(all_items))
        list_genres = item.get("genres_raw", [])
        detail_genres = detail.get("raw_genres", [])
        genres = norm_genres(list_genres + detail_genres)
        save_series({
            "source":             source_key,
            "slug":               item["slug"],
            "url":                url,
            "title":              item["title"],
            "cover_url":          item.get("cover",""),
            "description":        detail.get("description",""),
            "status":             detail.get("status","Devam Ediyor"),
            "content_type":       item.get("content_type","Manhwa"),
            "genres":             json.dumps(genres, ensure_ascii=False),
            "rating":             item.get("rating", 0.0),
            "view_count":         item.get("est_views", 0),
            "chapter_count":      detail.get("chapter_count", 0),
            "latest_chapter":     item.get("latest_chapter",""),
            "latest_chapter_url": (detail.get("chapters") or [{}])[0].get("url",""),
            "last_updated":       detail.get("last_updated") or datetime.now().isoformat(),
        })
        saved += 1
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO scrape_log(source,status,series_count,message) VALUES(?,?,?,?)",
              (source_key, "ok", saved, f"{saved} seri kaydedildi"))
    c.commit(); c.close()
    log.info("=== Bitti: %s — %d seri ===", source_key, saved)

def scrape_all():
    for k in SOURCES:
        try:
            run(k, full=True)
        except Exception as e:
            log.error("Hata (%s): %s", k, e)
        time.sleep(3)

def scheduler():
    # İlk başlatmada DB'de seri varsa beklemeyle başla (Railway IP engeli için)
    try:
        conn_tmp = sqlite3.connect(DB)
        series_count = conn_tmp.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        conn_tmp.close()
    except:
        series_count = 0
    if series_count > 0:
        log.info("DB'de %d seri mevcut, ilk scrape %d saat sonra yapilacak", series_count, INTERVAL)
        time.sleep(INTERVAL * 3600)
    else:
        log.info("DB bos, ilk scrape basliyor...")
    while True:
        scrape_all()
        log.info("Sonraki: %d saat sonra", INTERVAL)
        time.sleep(INTERVAL * 3600)

# ─── FLASK API ─────────────────────────────────
app = Flask(__name__)
CORS(app)
init_db()
Thread(target=scheduler, daemon=True).start()

# STATİK DOSYALAR
@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

@app.route('/reader')
@app.route('/reader.html')
def reader_page():
    return send_from_directory('.', 'reader.html')

@app.route('/admin')
@app.route('/admin.html')
def admin_page():
    return send_from_directory('.', 'admin.html')

# API ROUTES
@app.route("/api/trending")
def trending():
    ct = request.args.get("type","")
    src = request.args.get("source","")
    genre = request.args.get("genre","")
    limit = min(int(request.args.get("limit",10)),200)
    sql = "SELECT * FROM series WHERE 1=1"
    p = []
    if ct:    sql += " AND content_type=?"; p.append(ct)
    if src:   sql += " AND source=?";       p.append(src)
    if genre: sql += " AND genres LIKE ?";  p.append(f"%{genre}%")
    sql += " ORDER BY view_count DESC LIMIT ?"; p.append(limit)
    return jsonify({"status":"ok","data":rows(sql,p)})

@app.route("/api/latest")
def latest():
    ct = request.args.get("type","")
    src = request.args.get("source","")
    genre = request.args.get("genre","")
    limit = min(int(request.args.get("limit",12)),200)
    sql = "SELECT * FROM series WHERE 1=1"
    p = []
    if ct:    sql += " AND content_type=?"; p.append(ct)
    if src:   sql += " AND source=?";       p.append(src)
    if genre: sql += " AND genres LIKE ?";  p.append(f"%{genre}%")
    sql += " ORDER BY last_updated DESC LIMIT ?"; p.append(limit)
    return jsonify({"status":"ok","data":rows(sql,p)})

@app.route("/api/search")
def search():
    q = request.args.get("q","")
    ct = request.args.get("type","")
    genre = request.args.get("genre","")
    limit = min(int(request.args.get("limit",20)),100)
    sql = "SELECT * FROM series WHERE 1=1"
    p = []
    if q:     sql += " AND title LIKE ?";   p.append(f"%{q}%")
    if ct:    sql += " AND content_type=?"; p.append(ct)
    if genre: sql += " AND genres LIKE ?";  p.append(f"%{genre}%")
    sql += " ORDER BY view_count DESC LIMIT ?"; p.append(limit)
    data = rows(sql,p)
    return jsonify({"status":"ok","count":len(data),"data":data})

@app.route("/api/series/<path:slug>")
def series_detail(slug):
    data = rows("SELECT * FROM series WHERE slug=? OR url LIKE ?",
                (slug, f"%{slug}%"))
    if not data:
        return jsonify({"status":"error","message":"Bulunamadı"}), 404
    s = data[0]
    series_url = s["url"]
    chaps = rows("SELECT * FROM chapters WHERE series_url=? ORDER BY rowid DESC",
                 (series_url,))
    if not chaps:
        log.info("Bölüm listesi canlı çekiliyor: %s", series_url)
        sess = requests.Session()
        sess.headers.update(HEADERS)
        detail = scrape_detail(series_url, sess)
        if detail.get("chapters"):
            save_chapters(series_url, detail["chapters"])
            chaps = rows("SELECT * FROM chapters WHERE series_url=? ORDER BY rowid DESC",
                         (series_url,))
        if detail.get("description") and not s.get("description"):
            c = sqlite3.connect(DB)
            c.execute("UPDATE series SET description=?,genres=?,status=? WHERE url=?",
                     (detail["description"],
                      json.dumps(norm_genres(detail.get("raw_genres",[])), ensure_ascii=False),
                      detail.get("status","Devam Ediyor"),
                      series_url))
            c.commit(); c.close()
            s["description"] = detail["description"]
    s["chapters"] = chaps
    return jsonify({"status":"ok","data":s})

@app.route("/api/chapter/images")
def chapter_images():
    chap_url = request.args.get("url","")
    if not chap_url:
        return jsonify({"status":"error","message":"url parametresi gerekli"}), 400
    log.info("Bölüm resimleri çekiliyor: %s", chap_url)
    sess = requests.Session()
    sess.headers.update(HEADERS)
    images = scrape_chapter_images(chap_url, sess)
    return jsonify({"status":"ok","count":len(images),"images":images})

@app.route("/api/proxy/image")
def proxy_image():
    img_url = request.args.get("url","")
    if not img_url or not img_url.startswith("http"):
        return "Geçersiz URL", 400
    try:
        proxy_headers = dict(HEADERS)
        proxy_headers["Referer"] = "/".join(img_url.split("/")[:3]) + "/"
        r = requests.get(img_url, headers=proxy_headers, stream=True, timeout=15)
        content_type = r.headers.get("Content-Type","image/jpeg")
        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                yield chunk
        return Response(stream_with_context(generate()), content_type=content_type,
                       headers={"Cache-Control": "public, max-age=3600"})
    except Exception as e:
        log.error("Proxy hatası: %s", e)
        return "Resim yüklenemedi", 502

@app.route("/api/genres")
def genres():
    all_rows = rows("SELECT genres FROM series")
    cnt = {}
    for r in all_rows:
        for g in (r.get("genres") or []):
            cnt[g] = cnt.get(g,0)+1
    srt = sorted(cnt.items(), key=lambda x:-x[1])
    return jsonify({"status":"ok","data":[{"genre":g,"count":c} for g,c in srt]})

@app.route("/api/stats")
def stats():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    def one(sql): cur.execute(sql); return (cur.fetchone() or [0])[0]
    d = {
        "total_series":   one("SELECT COUNT(*) FROM series"),
        "manhwa":         one("SELECT COUNT(*) FROM series WHERE content_type='Manhwa'"),
        "manga":          one("SELECT COUNT(*) FROM series WHERE content_type='Manga'"),
        "manhua":         one("SELECT COUNT(*) FROM series WHERE content_type='Manhua'"),
        "total_chapters": one("SELECT COUNT(*) FROM chapters"),
    }
    cur.execute("SELECT created_at FROM scrape_log ORDER BY created_at DESC LIMIT 1")
    ls = cur.fetchone()
    d["last_scrape"] = ls[0] if ls else None
    c.close()
    return jsonify({"status":"ok","data":d})

@app.route("/api/scrape/now", methods=["POST"])
def trigger():
    src = (request.json or {}).get("source","all")
    Thread(target=scrape_all if src=="all" else lambda:run(src,True), daemon=True).start()
    return jsonify({"status":"ok","message":"Başlatıldı"})

@app.route("/api/logs")
def get_logs():
    limit = min(int(request.args.get("limit",20)), 200)
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT source,status,series_count,message,created_at FROM scrape_log ORDER BY created_at DESC LIMIT ?", (limit,))
    cols = ["source","status","series_count","message","created_at"]
    data = [dict(zip(cols,r)) for r in cur.fetchall()]
    c.close()
    return jsonify({"status":"ok","data":data})

@app.route("/api/series/delete", methods=["POST"])
def delete_series():
    url = (request.json or {}).get("url","")
    if not url:
        return jsonify({"status":"error","message":"URL gerekli"}), 400
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM chapters WHERE series_url=?", (url,))
    c.execute("DELETE FROM series WHERE url=?", (url,))
    c.commit()
    c.close()
    return jsonify({"status":"ok","message":"Silindi"})

@app.route("/api/db/clear", methods=["POST"])
def clear_db():
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM chapters")
    c.execute("DELETE FROM series")
    c.execute("DELETE FROM scrape_log")
    c.commit()
    c.close()
    return jsonify({"status":"ok","message":"Veritabanı temizlendi"})

# ─── KULLANICI SİSTEMİ ─────────────────────────
def hash_pwd(pwd): return hashlib.sha256(pwd.encode()).hexdigest()
def gen_token():   return secrets.token_hex(32)

def get_user_by_token(token):
    if not token: return None
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                   WHERE s.token=? AND (s.expires_at IS NULL OR s.expires_at>datetime('now'))""",(token,))
    row = cur.fetchone()
    if not row: c.close(); return None
    cols = [d[0] for d in cur.description]
    user = dict(zip(cols,row)); c.close()
    return user

def safe_user(u):
    if not u: return None
    d = dict(u)
    d.pop("password_hash", None)
    if "roles_earned" in d:
        try: d["roles_earned"] = json.loads(d["roles_earned"])
        except: d["roles_earned"] = ["Yeni"]
    return d

def require_admin(f):
    @wraps(f)
    def wrapper(*args,**kwargs):
        token = request.headers.get("Authorization","").replace("Bearer ","")
        if token == "admin_panel": return f(*args,**kwargs)
        user = get_user_by_token(token)
        if user and user.get("role")=="Admin": return f(*args,**kwargs)
        return jsonify({"status":"error","message":"Yetkisiz"}),403
    return wrapper

@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.json or {}
    email = (d.get("email","")).strip().lower()
    pwd   = d.get("password","")
    uname = (d.get("username","")).strip()
    if not email or not pwd or not uname:
        return jsonify({"status":"error","message":"Tüm alanlar zorunlu"}),400
    if len(pwd)<6: return jsonify({"status":"error","message":"Şifre en az 6 karakter"}),400
    if len(uname)<3: return jsonify({"status":"error","message":"Kullanıcı adı en az 3 karakter"}),400
    try:
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO users(email,password_hash,username) VALUES(?,?,?)",(email,hash_pwd(pwd),uname))
        c.commit()
        uid = c.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()[0]
        token = gen_token()
        c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)",(token,uid))
        c.commit(); c.close()
        return jsonify({"status":"ok","token":token,"message":"Kayıt başarılı"})
    except Exception as e:
        msg = "Bu e-posta veya kullanıcı adı zaten kullanılıyor" if "UNIQUE" in str(e) else str(e)
        return jsonify({"status":"error","message":msg}),400

@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.json or {}
    email = (d.get("email","")).strip().lower()
    pwd   = d.get("password","")
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT * FROM users WHERE email=? AND password_hash=?",(email,hash_pwd(pwd)))
    row = cur.fetchone()
    if not row: c.close(); return jsonify({"status":"error","message":"E-posta veya şifre yanlış"}),401
    cols = [d2[0] for d2 in cur.description]; user = dict(zip(cols,row))
    if user["is_banned"]: c.close(); return jsonify({"status":"error","message":"Hesabınız askıya alındı"}),403
    token = gen_token()
    c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)",(token,user["id"]))
    c.execute("UPDATE users SET last_login=datetime('now') WHERE id=?",(user["id"],))
    c.commit(); c.close()
    return jsonify({"status":"ok","token":token,"user":safe_user(user)})

@app.route("/api/auth/me")
def me():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error","message":"Giriş gerekli"}),401
    return jsonify({"status":"ok","user":safe_user(user)})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM sessions WHERE token=?",(token,))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route("/api/user/favorites", methods=["GET"])
def get_favorites():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error"}),401
    data = rows("SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC",(user["id"],))
    return jsonify({"status":"ok","data":data})

@app.route("/api/user/favorites", methods=["POST"])
def toggle_favorite():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error"}),401
    d = request.json or {}
    series_url   = d.get("series_url","")
    series_title = d.get("series_title","")
    series_cover = d.get("series_cover","")
    series_type  = d.get("series_type","")
    if not series_url: return jsonify({"status":"error"}),400
    c = sqlite3.connect(DB)
    existing = c.execute("SELECT id FROM favorites WHERE user_id=? AND series_url=?",(user["id"],series_url)).fetchone()
    if existing:
        c.execute("DELETE FROM favorites WHERE user_id=? AND series_url=?",(user["id"],series_url))
        c.commit(); c.close()
        return jsonify({"status":"ok","action":"removed"})
    else:
        c.execute("INSERT INTO favorites(user_id,series_url,series_title,series_cover,series_type) VALUES(?,?,?,?,?)",
                  (user["id"],series_url,series_title,series_cover,series_type))
        c.commit(); c.close()
        return jsonify({"status":"ok","action":"added"})

@app.route("/api/user/progress", methods=["GET"])
def get_progress():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error"}),401
    data = rows("SELECT * FROM user_progress WHERE user_id=? ORDER BY updated_at DESC",(user["id"],))
    return jsonify({"status":"ok","data":data})

@app.route("/api/user/progress", methods=["POST"])
def save_progress():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error"}),401
    d = request.json or {}
    series_url  = d.get("series_url","")
    chap_url    = d.get("last_chapter_url","")
    chap_num    = d.get("last_chapter_num","")
    status      = d.get("status","reading")
    ser_title   = d.get("series_title","")
    ser_cover   = d.get("series_cover","")
    if not series_url: return jsonify({"status":"error"}),400
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO user_progress
                 (user_id,series_url,series_title,series_cover,last_chapter_url,last_chapter_num,status,updated_at)
                 VALUES(?,?,?,?,?,?,?,datetime('now'))
                 ON CONFLICT(user_id,series_url) DO UPDATE SET
                 last_chapter_url=excluded.last_chapter_url,
                 last_chapter_num=excluded.last_chapter_num,
                 series_title=excluded.series_title,
                 series_cover=excluded.series_cover,
                 status=excluded.status,
                 updated_at=datetime('now')""",
              (user["id"],series_url,ser_title,ser_cover,chap_url,chap_num,status))
    completed = c.execute("SELECT COUNT(*) FROM user_progress WHERE user_id=? AND status='completed'",(user["id"],)).fetchone()[0]
    reading   = c.execute("SELECT COUNT(*) FROM user_progress WHERE user_id=? AND status='reading'",(user["id"],)).fetchone()[0]
    cur_role  = c.execute("SELECT role,roles_earned FROM users WHERE id=?",(user["id"],)).fetchone()
    cur_main_role = cur_role[0] if cur_role else "Yeni"
    cur_earned = cur_role[1] if cur_role else '["Yeni"]'
    if cur_main_role != "Admin":
        def auto_role(c): return "Efsane" if c>=50 else "Uzman" if c>=20 else "Deneyimli" if c>=5 else "Yeni"
        new_role = auto_role(completed)
        try: earned_list = json.loads(cur_earned)
        except: earned_list = ["Yeni"]
        if new_role not in earned_list:
            earned_list.append(new_role)
        c.execute("UPDATE users SET completed_count=?,reading_count=?,role=?,roles_earned=? WHERE id=?",
                  (completed, reading, new_role, json.dumps(earned_list, ensure_ascii=False), user["id"]))
    else:
        c.execute("UPDATE users SET completed_count=?,reading_count=? WHERE id=?",
                  (completed, reading, user["id"]))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route("/api/user/profile", methods=["POST"])
def update_profile():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error"}),401
    d    = request.json or {}
    bio  = d.get("bio","")[:300]
    name = (d.get("username","")).strip()
    avatar_url = d.get("avatar_url","")
    c    = sqlite3.connect(DB)
    updates, params = ["bio=?"], [bio]
    if name and len(name)>=3:
        updates.append("username=?"); params.append(name)
    if avatar_url:
        updates.append("avatar_url=?"); params.append(avatar_url)
    params.append(user["id"])
    try:
        c.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", params)
    except Exception as e:
        c.execute("UPDATE users SET bio=? WHERE id=?", (bio, user["id"]))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route("/api/user/avatar", methods=["POST"])
def upload_avatar():
    token = request.headers.get("Authorization","").replace("Bearer ","")
    user  = get_user_by_token(token)
    if not user: return jsonify({"status":"error","message":"Giriş gerekli"}),401
    d = request.json or {}
    avatar_url = d.get("avatar_url","")
    if not avatar_url:
        return jsonify({"status":"error","message":"URL gerekli"}),400
    if len(avatar_url) > 700000:
        return jsonify({"status":"error","message":"Resim çok büyük (max 500KB)"}),400
    c = sqlite3.connect(DB)
    c.execute("UPDATE users SET avatar_url=? WHERE id=?",(avatar_url, user["id"]))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

# ADMIN API
@app.route("/api/admin/users")
@require_admin
def admin_users():
    limit  = min(int(request.args.get("limit",100)),500)
    offset = int(request.args.get("offset",0))
    q      = request.args.get("q","")
    sql    = "SELECT id,email,username,role,custom_role,completed_count,reading_count,is_banned,created_at,last_login FROM users WHERE 1=1"
    p      = []
    if q:  sql += " AND (username LIKE ? OR email LIKE ?)"; p+=[f"%{q}%",f"%{q}%"]
    sql   += " ORDER BY created_at DESC LIMIT ? OFFSET ?"; p+=[limit,offset]
    c = sqlite3.connect(DB); cur = c.cursor(); cur.execute(sql,p)
    cols = [d[0] for d in cur.description]
    data = [dict(zip(cols,r)) for r in cur.fetchall()]
    total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    c.close()
    return jsonify({"status":"ok","total":total,"data":data})

@app.route("/api/admin/users/<int:uid>/role", methods=["POST"])
@require_admin
def set_role(uid):
    d      = request.json or {}
    role   = d.get("role","Yeni")
    custom = d.get("custom_role","")
    c = sqlite3.connect(DB)
    c.execute("UPDATE users SET role=?,custom_role=? WHERE id=?",(role,custom,uid))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route("/api/admin/users/<int:uid>/ban", methods=["POST"])
@require_admin
def ban_user(uid):
    banned = (request.json or {}).get("banned",True)
    c = sqlite3.connect(DB)
    c.execute("UPDATE users SET is_banned=? WHERE id=?",(1 if banned else 0,uid))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route("/api/admin/series/update", methods=["POST"])
@require_admin
def admin_update_series():
    d      = request.json or {}
    url    = d.get("url","")
    if not url: return jsonify({"status":"error"}),400
    c = sqlite3.connect(DB)
    updates,params = [],[]
    if d.get("title"):        updates.append("title=?");        params.append(d["title"])
    if d.get("status"):       updates.append("status=?");       params.append(d["status"])
    if d.get("content_type"): updates.append("content_type=?"); params.append(d["content_type"])
    if d.get("genres"):       updates.append("genres=?");       params.append(json.dumps(d["genres"],ensure_ascii=False))
    if updates:
        params.append(url)
        c.execute(f"UPDATE series SET {','.join(updates)} WHERE url=?",params)
        c.commit()
    c.close()
    return jsonify({"status":"ok"})

@app.route("/api/settings")
def get_settings():
    c = sqlite3.connect(DB); cur = c.cursor()
    cur.execute("SELECT key,value FROM site_settings")
    data = {r[0]:r[1] for r in cur.fetchall()}; c.close()
    try: data["hero_banners"] = json.loads(data.get("hero_banners","[]"))
    except: data["hero_banners"] = []
    return jsonify({"status":"ok","data":data})

@app.route("/api/settings", methods=["POST"])
@require_admin
def save_settings():
    d = request.json or {}
    c = sqlite3.connect(DB)
    for key,val in d.items():
        if isinstance(val,(list,dict)): val=json.dumps(val,ensure_ascii=False)
        c.execute("INSERT INTO site_settings(key,value,updated_at) VALUES(?,?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=datetime('now')", (key,str(val)))
    c.commit(); c.close()
    return jsonify({"status":"ok"})

@app.route('/api/upload-db', methods=['POST'])
def upload_db():
    data = request.get_data()
    if not data:
        return jsonify({"status":"error","message":"Dosya boş"}), 400
    try:
        with open("voidscans.db", "wb") as f:
            f.write(data)
        return jsonify({"status":"ok","message":"Veritabanı yüklendi"})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

if __name__ == "__main__":
    init_db()
    Thread(target=scheduler, daemon=True).start()
    log.info("API: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
